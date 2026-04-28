from decimal import Decimal, InvalidOperation
from html import unescape
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from meherloom.models import Product
from meherloom.services.scrapers.generic import GenericBrandAdapter


class SapphireBrandAdapter(GenericBrandAdapter):
    title_heading_pattern = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
    sku_pattern = re.compile(r"SKU:\s*([A-Z0-9_-]+)", re.IGNORECASE)
    price_pattern = re.compile(r"Rs\.\s*([0-9,]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)
    text_title_sku_pattern = re.compile(
        r"([A-Za-z0-9][A-Za-z0-9 &'()/,\-]{5,160}?)\s+Rs\.\s*[0-9,]+(?:\.[0-9]{1,2})?\s+SKU:\s*([A-Z0-9_-]+)",
        re.IGNORECASE,
    )
    image_pattern = re.compile(r"https://[^\"'\s>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'\s>]*)?", re.IGNORECASE)
    detail_block_pattern = re.compile(
        r"Description\s+(.*?)(?:Share this Look|Notify me when available|We['’]ll notify you|The link to|$)",
        re.IGNORECASE | re.DOTALL,
    )
    size_input_pattern = re.compile(
        r'(?:Select your size|SIZE:)(.*?)(?:Size Chart|sale starts in|Quantity:|Details|Description)',
        re.IGNORECASE | re.DOTALL,
    )
    size_token_pattern = re.compile(r">\s*([A-Z0-9-]{1,8})\s*<")

    def fetch_product(self, product):
        html = self.fetch_url(product.source_url)
        product_data = self._extract_product_from_json_ld(html) or {}
        meta = self._extract_meta(html)
        page_text = self._normalized_page_text(html)

        payload = {
            "title": self._extract_title(product_data, meta, html, page_text),
            "description": self._extract_description(product_data, meta, html, page_text),
            "source_product_id": self._extract_product_id(product_data, product.source_url),
            "source_sku": self._extract_sku(product_data, html, page_text),
            "source_currency": product_data.get("offers", {}).get("priceCurrency", "") if isinstance(product_data.get("offers"), dict) else "",
            "source_price": self._extract_price(product_data, html, page_text),
            "stock_status": self._extract_sapphire_stock_status(product_data, html, page_text),
            "stock_quantity": self._extract_stock_quantity(product_data),
            "image_urls": self._extract_sapphire_images(product_data, meta, html),
            "variants": self._extract_sapphire_variants(product_data, html, page_text),
        }

        if not payload["source_product_id"]:
            payload["source_product_id"] = payload["source_sku"]
        return payload

    def _extract_title(self, product_data, meta, html, page_text):
        text_match = self.text_title_sku_pattern.search(page_text)
        if text_match:
            return self._clean_title(text_match.group(1))

        heading_matches = self.title_heading_pattern.findall(html)
        for heading in heading_matches:
            title = self._strip_html(heading)
            if title and "sapphire" not in title.lower():
                return self._clean_title(title)

        title = product_data.get("name") or meta.get("og:title") or meta.get("title") or ""
        if title:
            return self._clean_title(title)
        return ""

    def _extract_description(self, product_data, meta, html, page_text):
        match = self.detail_block_pattern.search(page_text)
        if match:
            description = self._clean_description(match.group(1))
            if description:
                return re.sub(r"\s+", " ", description).strip()
        return ""

    def _extract_sku(self, product_data, html, page_text):
        text_match = self.text_title_sku_pattern.search(page_text)
        if text_match:
            return text_match.group(2).strip()

        match = self.sku_pattern.search(page_text)
        if match:
            return match.group(1).strip()
        sku = str(product_data.get("sku") or "").strip()
        if sku and sku != "contextSecondaryAUIDs":
            return sku
        return ""

    def _extract_price(self, product_data, html, page_text):
        structured_price, _currency = self._extract_price_from_structured_data(product_data)
        if structured_price is not None:
            return structured_price

        match = self.price_pattern.search(page_text)
        if not match:
            return None
        raw = match.group(1).replace(",", "")
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None

    def _extract_price_from_structured_data(self, product_data):
        offers = product_data.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        offers = offers or {}
        price_raw = offers.get("price")
        currency = offers.get("priceCurrency", "")
        if price_raw in (None, ""):
            return None, currency
        try:
            return Decimal(str(price_raw)), currency
        except InvalidOperation:
            return None, currency

    def _extract_sapphire_stock_status(self, product_data, html, page_text):
        html_lower = html.lower()
        page_text_lower = page_text.lower()
        has_add_to_bag = "add to bag" in page_text_lower
        has_notify_me = "notify me when available" in page_text_lower
        has_sale_timer = "sale starts in" in page_text_lower

        if has_notify_me and not has_add_to_bag:
            return Product.StockStatus.OUT_OF_STOCK

        structured_status = self._extract_stock_status(product_data, page_text)
        if structured_status != Product.StockStatus.UNKNOWN:
            return structured_status

        if has_add_to_bag:
            return Product.StockStatus.IN_STOCK
        if has_sale_timer and has_add_to_bag:
            return Product.StockStatus.IN_STOCK
        return Product.StockStatus.UNKNOWN

    def _extract_sapphire_variants(self, product_data, html, page_text):
        variants = self._extract_variants(product_data)
        if variants:
            return variants

        html_lower = page_text.lower()
        stock_status = self._extract_sapphire_stock_status(product_data, html, page_text)
        size_match = self.size_input_pattern.search(html)
        if not size_match:
            return []

        sizes_block = size_match.group(1)
        seen = set()
        extracted = []
        for size in self.size_token_pattern.findall(sizes_block):
            cleaned = size.strip().upper()
            if cleaned in {"INPUT", "BUTTON"} or cleaned in seen:
                continue
            seen.add(cleaned)
            extracted.append(
                {
                    "name": cleaned,
                    "source_variant_id": "",
                    "source_sku": "",
                    "stock_status": stock_status if "select your size" in html_lower or "size:" in html_lower else Product.StockStatus.UNKNOWN,
                    "stock_quantity": None,
                }
            )
        return extracted

    def _extract_sapphire_images(self, product_data, meta, html):
        images = self._extract_images(product_data, meta)
        if images:
            return self._dedupe_images(images)

        found = []
        seen = set()
        for image_url in self.image_pattern.findall(html):
            normalized = self._canonical_image_url(self._normalize_url(image_url))
            if normalized in seen:
                continue
            seen.add(normalized)
            found.append(self._normalize_url(image_url))
        return found

    def _extract_product_id(self, product_data, source_url):
        product_id = str(product_data.get("productID") or "").strip()
        if product_id and product_id != "contextSecondaryAUIDs":
            return product_id
        path = urlparse(source_url).path.rstrip("/").split("/")[-1]
        return path.replace(".html", "")

    def _normalized_page_text(self, html):
        text = self._strip_html(html)
        return re.sub(r"\s+", " ", text).strip()

    def _clean_title(self, title):
        cleaned = re.sub(r"^.*?Home\s+Woman\s+Unstitched\s+", "", title, flags=re.IGNORECASE).strip()
        duplicate_match = re.search(
            r"((?:\d+\s+Piece\s*-\s*[A-Za-z][A-Za-z ]+Suit))(?:\s+\1)+",
            cleaned,
            flags=re.IGNORECASE,
        )
        if duplicate_match:
            cleaned = duplicate_match.group(1)
        cleaned = re.sub(r"\s+Sapphire\s+PK$", "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned

    def _clean_description(self, description):
        cleaned = re.sub(r"\.blink\s*\{.*$", "", description, flags=re.IGNORECASE)
        cleaned = re.sub(r"@keyframes\s+.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bPrevious\s+Next\b.*$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _canonical_image_url(self, image_url):
        image_url = unescape(image_url)
        parsed = urlparse(image_url)
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in {"sw", "sh"}
        ]
        return urlunparse(parsed._replace(query=urlencode(filtered_query)))

    def _dedupe_images(self, images):
        deduped = []
        seen = set()
        for image_url in images:
            canonical = self._canonical_image_url(image_url)
            if canonical in seen:
                continue
            seen.add(canonical)
            deduped.append(image_url)
        return deduped
