from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlparse

from meherloom.models import Product
from meherloom.services.scrapers.generic import GenericBrandAdapter
from meherloom.services.scrapers.shopify import ShopifyBrandAdapter


class AghaNoorBrandAdapter(GenericBrandAdapter):
    title_heading_pattern = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
    price_pattern = re.compile(r"Rs\.?\s*([0-9,]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)
    sku_pattern = re.compile(r"\b([A-Z]{1,6}[0-9]{2,}[A-Z0-9_-]*)\b")
    size_block_pattern = re.compile(
        r"Size:\s*(.*?)(?:Color:|Colour:|Product variants|Description|Shipping & Return|Free Shipping)",
        re.IGNORECASE | re.DOTALL,
    )
    color_block_pattern = re.compile(
        r"(?:Color|Colour):\s*(.*?)(?:Product variants|Description|Shipping & Return|Free Shipping)",
        re.IGNORECASE | re.DOTALL,
    )
    token_pattern = re.compile(r">\s*([^<>]{1,40})\s*<")

    def fetch_product(self, product):
        if self._is_bridal_product(product.source_url):
            payload = ShopifyBrandAdapter(self.brand).fetch_product(product)
            payload["description"] = self._clean_bridal_description(payload.get("description", ""))
            return payload

        html = self.fetch_url(product.source_url)
        product_data = (
            self._extract_product_from_json_ld(html)
            or self._extract_product_from_embedded_json(html)
            or {}
        )
        meta = self._extract_meta(html)

        title = self._extract_title(product_data, meta, html)
        description = self._extract_description(product_data, meta, html)
        sku = self._extract_sku(product_data, title, html)
        price, currency = self._extract_price(product_data, html)
        stock_status = self._extract_agha_noor_stock_status(product_data, html)
        variants = self._extract_agha_noor_variants(product_data, html, stock_status)
        image_urls = self._extract_images(product_data, meta)

        return {
            "title": title,
            "description": description,
            "source_product_id": str(product_data.get("productID") or sku or ""),
            "source_sku": sku,
            "source_currency": currency,
            "source_price": price,
            "stock_status": stock_status,
            "stock_quantity": self._extract_stock_quantity(product_data),
            "image_urls": image_urls,
            "variants": variants,
        }

    def _extract_title(self, product_data, meta, html):
        title = product_data.get("name") or meta.get("og:title") or meta.get("title") or ""
        if title:
            return title.strip()
        match = self.title_heading_pattern.search(html)
        return self._strip_html(match.group(1)) if match else ""

    def _extract_description(self, product_data, meta, html):
        description = product_data.get("description") or meta.get("description") or meta.get("og:description") or ""
        if description:
            return description.strip()
        return ""

    def _extract_sku(self, product_data, title, html):
        sku = str(product_data.get("sku") or "").strip()
        if sku:
            return sku
        title_match = self.sku_pattern.search(title or "")
        if title_match:
            return title_match.group(1)
        html_match = self.sku_pattern.search(html)
        return html_match.group(1) if html_match else ""

    def _extract_price(self, product_data, html):
        structured_price, currency = self._extract_price_from_structured_data(product_data)
        if structured_price is not None:
            return structured_price, currency or "PKR"

        match = self.price_pattern.search(html)
        if not match:
            return None, "PKR"
        raw = match.group(1).replace(",", "")
        try:
            return Decimal(raw), "PKR"
        except InvalidOperation:
            return None, "PKR"

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

    def _extract_agha_noor_stock_status(self, product_data, html):
        html_lower = html.lower()
        has_add_to_cart = "add to cart" in html_lower
        has_sold_out = "sold out" in html_lower
        has_back_in_stock_form = "notify as soon as the product / variant is back in stock" in html_lower

        if has_sold_out and not has_add_to_cart:
            return Product.StockStatus.OUT_OF_STOCK
        if has_back_in_stock_form and not has_add_to_cart:
            return Product.StockStatus.OUT_OF_STOCK

        structured_status = self._extract_stock_status(product_data, html)
        if structured_status != Product.StockStatus.UNKNOWN:
            return structured_status
        if has_add_to_cart:
            return Product.StockStatus.IN_STOCK
        return Product.StockStatus.UNKNOWN

    def _extract_agha_noor_variants(self, product_data, html, stock_status):
        variants = self._clean_variants(self._extract_variants(product_data))
        if variants:
            return variants

        size_values = self._extract_choice_values(html, self.size_block_pattern)
        color_values = self._extract_choice_values(html, self.color_block_pattern)

        if not size_values and not color_values:
            return []

        if size_values and color_values:
            return [
                {
                    "name": f"{size} / {color}",
                    "source_variant_id": "",
                    "source_sku": "",
                    "stock_status": stock_status,
                    "stock_quantity": None,
                }
                for size in size_values
                for color in color_values
            ]

        base_values = size_values or color_values
        return [
            {
                "name": value,
                "source_variant_id": "",
                "source_sku": "",
                "stock_status": stock_status,
                "stock_quantity": None,
            }
            for value in base_values
        ]

    def _extract_choice_values(self, html, pattern):
        match = pattern.search(html)
        if not match:
            return []

        block = match.group(1)
        values = []
        seen = set()
        for token in self.token_pattern.findall(block):
            cleaned = self._strip_html(token).strip()
            if not cleaned:
                continue
            upper = cleaned.upper()
            if upper in {"INPUT", "SELECT", "BUTTON"}:
                continue
            if not self._looks_like_choice_value(cleaned):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            values.append(cleaned)
        return values

    def _clean_variants(self, variants):
        cleaned_variants = []
        seen = set()
        for variant in variants:
            name = (variant.get("name") or "").strip()
            if not self._looks_like_variant_name(name):
                continue
            key = (
                name.lower(),
                (variant.get("source_sku") or "").strip().lower(),
                (variant.get("source_variant_id") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            cleaned_variants.append(variant)
        return cleaned_variants

    def _looks_like_variant_name(self, name):
        if not name:
            return False
        lowered = name.lower()
        if any(marker in lowered for marker in ("appendto(", "scappshop", "{{amount", "$m(", '").', 'head");')):
            return False
        if "{{" in name or "}}" in name:
            return False
        return True

    def _looks_like_choice_value(self, value):
        lowered = value.lower()
        if any(marker in lowered for marker in ("add to cart", "sold out", "free shipping", "shipping & return")):
            return False
        if len(value) > 30:
            return False
        return True

    def _is_bridal_product(self, source_url):
        return "aghanoorbridal.com" in urlparse(source_url).netloc.lower()

    def _clean_bridal_description(self, description):
        cleaned = re.sub(r"\s+", " ", description or "").strip()
        if not cleaned:
            return ""

        patterns = (
            r"Delivery Date:\s*.*?(?=(?:For further queries|$))",
            r"For further queries/customization/orders call or WhatsApp on:\s*.*$",
            r"For further queries\s*/\s*customization\s*/\s*orders call or WhatsApp on:\s*.*$",
            r"\+?\d[\d\s-]{7,}$",
        )
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|")
        return cleaned
