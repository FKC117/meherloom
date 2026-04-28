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
        r"Description\s+(.*?)(?:Share this Look|Notify me when available|We'll notify you|The link to|$)",
        re.IGNORECASE | re.DOTALL,
    )
    size_guide_modal_pattern = re.compile(
        r"Size Guide\s+(.*?)(?:Model Wears|Description|Details|Share this Look|Add to Bag|Notify Me|sale starts in|Note:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    size_input_pattern = re.compile(
        r'(?:Select your size|SIZE:)(.*?)(?:Size Chart|sale starts in|Quantity:|Details|Description)',
        re.IGNORECASE | re.DOTALL,
    )
    size_token_pattern = re.compile(r">\s*([A-Z0-9-]{1,8})\s*<")
    size_guide_row_pattern = re.compile(r"\b(XXS|XS|S|M|L|XL|XXL)\b\s*:?\s*([0-9]{2,3}(?:\.[0-9]+)?)?", re.IGNORECASE)
    size_marker_pattern = re.compile(r"\bSize\s+(?=(?:XXS|XS|S|M|L|XL|XXL)\b)", re.IGNORECASE)
    size_guide_title_pattern = re.compile(r"Size Guide\s+([A-Za-z][A-Za-z -]{2,80})\s+INCHES", re.IGNORECASE)
    size_guide_labels = (
        "Length",
        "Shoulder",
        "Chest",
        "Front Border",
        "Arm Hole",
        "Sleeve Length",
        "Sleeve Opening",
    )

    def fetch_product(self, product):
        html = self.fetch_url(product.source_url)
        product_data = self._extract_product_from_json_ld(html) or {}
        meta = self._extract_meta(html)
        page_text = self._normalized_page_text(html)

        payload = {
            "title": self._extract_title(product_data, meta, html, page_text),
            "description": self._extract_description(product_data, meta, html, page_text),
            "size_guide": self._extract_size_guide(html, page_text),
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
            candidate = self._clean_title(text_match.group(1))
            if self._looks_like_real_title(candidate):
                return candidate

        heading_matches = self.title_heading_pattern.findall(html)
        for heading in heading_matches:
            title = self._strip_html(heading)
            cleaned = self._clean_title(title)
            if self._looks_like_real_title(cleaned):
                return cleaned

        title = product_data.get("name") or meta.get("og:title") or meta.get("title") or ""
        if title:
            cleaned = self._clean_title(title)
            if self._looks_like_real_title(cleaned):
                return cleaned
        return ""

    def _extract_description(self, product_data, meta, html, page_text):
        match = self.detail_block_pattern.search(page_text)
        if match:
            description = self._clean_description(match.group(1))
            if description:
                description = self._strip_size_guide_from_description(description)
                return re.sub(r"\s+", " ", description).strip()
        return ""

    def _extract_size_guide(self, html, page_text):
        for raw_source in self._candidate_size_guide_sources(html, page_text):
            cleaned = self._strip_html(raw_source)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|:")
            if not cleaned:
                continue
            if self._looks_like_broken_size_guide(cleaned):
                continue

            structured_html = self._build_size_guide_html(cleaned)
            if structured_html:
                return structured_html
            if self.size_marker_pattern.search(cleaned) or "inches cm" in cleaned.lower():
                return cleaned
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
        page_prices = self._extract_page_prices(page_text)
        structured_price, _currency = self._extract_price_from_structured_data(product_data)

        if page_prices:
            if len(page_prices) == 1:
                page_price = page_prices[0]
            else:
                page_price = min(page_prices)

            if structured_price is None:
                return page_price
            if page_price <= structured_price:
                return page_price
            return structured_price

        return structured_price

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

    def _extract_page_prices(self, page_text):
        prices = []
        seen = set()
        for raw_price in self.price_pattern.findall(page_text):
            try:
                price = Decimal(raw_price.replace(",", ""))
            except InvalidOperation:
                continue
            if price in seen:
                continue
            seen.add(price)
            prices.append(price)
        return prices

    def _extract_sapphire_stock_status(self, product_data, html, page_text):
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

    def _looks_like_real_title(self, title):
        if not title:
            return False
        lowered = title.lower().strip()
        if "sapphire" in lowered:
            return False
        if re.fullmatch(r"[0-9,\s.]+(?:to)?", lowered):
            return False
        if lowered.endswith(" to"):
            return False
        if lowered.startswith("rs"):
            return False
        if len(title) < 4:
            return False
        return True

    def _clean_description(self, description):
        cleaned = re.sub(r"\.blink\s*\{.*$", "", description, flags=re.IGNORECASE)
        cleaned = re.sub(r"@keyframes\s+.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bPrevious\s+Next\b.*$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _candidate_size_guide_sources(self, html, page_text):
        candidates = []
        match = self.size_guide_modal_pattern.search(html)
        if match:
            candidates.append(match.group(0))
        match = self.size_guide_modal_pattern.search(page_text)
        if match:
            candidates.append(match.group(0))
        if "size guide" in page_text.lower():
            candidates.append(page_text)
        return candidates

    def _looks_like_broken_size_guide(self, cleaned_text):
        lowered = cleaned_text.lower()
        return any(
            marker in lowered
            for marker in ("tab-pane", "detail-content-pane", 'id="nav-details', "fade show active")
        )

    def _strip_size_guide_from_description(self, description):
        description = re.sub(
            r"Size Guide\s+[A-Za-z -]{0,80}",
            "",
            description,
            flags=re.IGNORECASE,
        )
        description = re.sub(
            r"INCHES\s+CM\s+Size\s+(?:XXS|XS|S|M|L|XL|XXL).*$",
            "",
            description,
            flags=re.IGNORECASE,
        )
        return description.strip()

    def _build_size_guide_html(self, cleaned_text):
        title_match = self.size_guide_title_pattern.search(cleaned_text)
        guide_title = title_match.group(1).strip() if title_match else ""

        header_match = self.size_marker_pattern.search(cleaned_text)
        if not header_match:
            return ""
        trailing_text = cleaned_text[header_match.end():]
        sizes = []
        remainder_tokens = []
        collecting_sizes = True
        for token in trailing_text.split():
            normalized = token.upper().strip()
            if collecting_sizes and normalized in {"XXS", "XS", "S", "M", "L", "XL", "XXL"}:
                sizes.append(normalized)
                continue
            collecting_sizes = False
            remainder_tokens.append(token)
        if not sizes:
            return ""

        rows = []
        values_text = " ".join(remainder_tokens)
        for label in self.size_guide_labels:
            pattern = re.compile(
                rf"{re.escape(label)}\s+((?:[0-9]+(?:\.[0-9]+)?\s+){{{max(len(sizes) - 1, 0)}}}[0-9]+(?:\.[0-9]+)?)",
                re.IGNORECASE,
            )
            match = pattern.search(values_text)
            if not match:
                continue
            values = match.group(1).split()
            if len(values) != len(sizes):
                continue
            rows.append((label, values))

        if not rows:
            fallback_rows = []
            for size, measurement in self.size_guide_row_pattern.findall(cleaned_text):
                fallback_rows.append(f"{size.upper()}: {measurement.strip()}" if measurement else size.upper())
            if fallback_rows:
                return "<div class=\"space-y-2 text-sm text-ink/75\">" + "".join(
                    f"<p>{row}</p>" for row in fallback_rows
                ) + "</div>"
            return ""

        header_cells = "".join(
            f"<th class=\"border border-black/10 px-3 py-3 text-center font-bold text-ink\">{size}</th>"
            for size in sizes
        )
        body_rows = []
        for label, values in rows:
            value_cells = "".join(
                f"<td class=\"border border-black/10 px-3 py-3 text-center text-ink/80\">{value}</td>"
                for value in values
            )
            body_rows.append(
                "<tr>"
                f"<th class=\"border border-black/10 bg-[#f7f1ea] px-4 py-3 text-left text-sm font-extrabold uppercase tracking-[0.12em] text-cocoa\">{label}</th>"
                f"{value_cells}"
                "</tr>"
            )

        subtitle = f"<p class=\"text-lg font-bold text-ink\">{guide_title}</p>" if guide_title else ""
        return (
            "<div class=\"space-y-5\">"
            f"{subtitle}"
            "<div class=\"overflow-x-auto rounded-2xl border border-black/10 bg-white\">"
            "<table class=\"min-w-full border-collapse text-sm\">"
            "<thead><tr>"
            "<th class=\"border border-black/10 bg-[#fdfaf6] px-4 py-3 text-left text-sm font-extrabold uppercase tracking-[0.12em] text-cocoa\">Size</th>"
            f"{header_cells}"
            "</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
            "</div>"
            "</div>"
        )

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
