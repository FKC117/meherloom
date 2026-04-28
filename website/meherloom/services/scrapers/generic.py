import json
import re
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import urljoin

from meherloom.models import Product
from meherloom.services.scrapers.base import BaseBrandAdapter


class GenericBrandAdapter(BaseBrandAdapter):
    json_ld_pattern = re.compile(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    meta_pattern = re.compile(
        r'<meta[^>]+(?:property|name)="(?P<name>[^"]+)"[^>]+content="(?P<content>[^"]*)"',
        re.IGNORECASE,
    )
    script_pattern = re.compile(r"<script[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    shopify_meta_pattern = re.compile(r"var\s+meta\s*=\s*(\{.*?\});", re.IGNORECASE | re.DOTALL)
    next_data_pattern = re.compile(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    nuxt_data_pattern = re.compile(
        r"__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>",
        re.IGNORECASE | re.DOTALL,
    )
    product_json_pattern = re.compile(
        r'/products/[^"\']+\.js',
        re.IGNORECASE,
    )
    availability_terms = {
        "in stock": Product.StockStatus.IN_STOCK,
        "available": Product.StockStatus.IN_STOCK,
        "out of stock": Product.StockStatus.OUT_OF_STOCK,
        "sold out": Product.StockStatus.OUT_OF_STOCK,
        "unavailable": Product.StockStatus.OUT_OF_STOCK,
        "preorder": Product.StockStatus.IN_STOCK,
        "pre-order": Product.StockStatus.IN_STOCK,
    }

    def fetch_product(self, product):
        html = self.fetch_url(product.source_url)
        product_data = (
            self._extract_product_from_json_ld(html)
            or self._extract_product_from_embedded_json(html)
            or {}
        )
        meta = self._extract_meta(html)

        title = product_data.get("name") or meta.get("og:title") or meta.get("title") or ""
        description = product_data.get("description") or meta.get("description") or meta.get("og:description") or ""
        image_urls = self._extract_images(product_data, meta)
        source_price, source_currency = self._extract_price(product_data)
        stock_status = self._extract_stock_status(product_data, html)
        variants = self._extract_variants(product_data)

        return {
            "title": title.strip(),
            "description": description.strip(),
            "source_product_id": str(product_data.get("productID") or product_data.get("sku") or ""),
            "source_sku": str(product_data.get("sku") or ""),
            "source_currency": source_currency,
            "source_price": source_price,
            "stock_status": stock_status,
            "stock_quantity": self._extract_stock_quantity(product_data),
            "image_urls": image_urls,
            "variants": variants,
        }

    def _extract_product_from_json_ld(self, html):
        for match in self.json_ld_pattern.findall(html):
            raw = match.strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            product = self._walk_json_ld(payload)
            if product:
                return product
        return None

    def _extract_product_from_embedded_json(self, html):
        data_sources = []

        next_data_match = self.next_data_pattern.search(html)
        if next_data_match:
            payload = self.parse_json_candidate(unescape(next_data_match.group(1)))
            if payload:
                data_sources.append(payload)

        nuxt_match = self.nuxt_data_pattern.search(html)
        if nuxt_match:
            payload = self.parse_json_candidate(unescape(nuxt_match.group(1)))
            if payload:
                data_sources.append(payload)

        shopify_match = self.shopify_meta_pattern.search(html)
        if shopify_match:
            payload = self.parse_json_candidate(unescape(shopify_match.group(1)))
            if payload:
                data_sources.append(payload)

        for script_body in self.script_pattern.findall(html):
            script_body = unescape(script_body.strip())
            payload = self.parse_json_candidate(script_body)
            if not payload:
                continue
            data_sources.append(payload)

        for payload in data_sources:
            product = self._walk_product_like_data(payload)
            if product:
                return product
        return None

    def _walk_json_ld(self, payload):
        if isinstance(payload, list):
            for item in payload:
                product = self._walk_json_ld(item)
                if product:
                    return product
            return None

        if not isinstance(payload, dict):
            return None

        payload_type = payload.get("@type")
        if payload_type == "Product" or (isinstance(payload_type, list) and "Product" in payload_type):
            return payload

        for key in ("@graph", "mainEntity", "itemListElement"):
            nested = payload.get(key)
            if nested:
                product = self._walk_json_ld(nested)
                if product:
                    return product
        return None

    def _walk_product_like_data(self, payload):
        if isinstance(payload, list):
            for item in payload:
                product = self._walk_product_like_data(item)
                if product:
                    return product
            return None

        if not isinstance(payload, dict):
            return None

        if self._looks_like_product_payload(payload):
            return self._normalize_product_payload(payload)

        for value in payload.values():
            product = self._walk_product_like_data(value)
            if product:
                return product
        return None

    def _looks_like_product_payload(self, payload):
        keys = set(payload.keys())
        strong_signals = [
            {"title", "variants"},
            {"name", "offers"},
            {"product", "variants"},
            {"sku", "price"},
            {"images", "variants"},
        ]
        return any(signal.issubset(keys) for signal in strong_signals)

    def _normalize_product_payload(self, payload):
        product = {}
        title = payload.get("name") or payload.get("title") or ""
        description = payload.get("description") or payload.get("body_html") or payload.get("body") or ""
        product["name"] = self._strip_html(title)
        product["description"] = self._strip_html(description)
        product["sku"] = str(payload.get("sku") or payload.get("id") or "")
        product["productID"] = str(payload.get("productID") or payload.get("id") or "")

        images = payload.get("image") or payload.get("images") or payload.get("media") or []
        normalized_images = []
        if isinstance(images, str):
            normalized_images = [images]
        elif isinstance(images, list):
            for image in images:
                if isinstance(image, str):
                    normalized_images.append(image)
                elif isinstance(image, dict):
                    image_url = image.get("src") or image.get("url")
                    if image_url:
                        normalized_images.append(image_url)
        elif isinstance(images, dict):
            image_url = images.get("src") or images.get("url")
            if image_url:
                normalized_images = [image_url]
        product["image"] = normalized_images

        offers = payload.get("offers")
        if not offers:
            variants = payload.get("variants", [])
            offers = []
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                offers.append(
                    {
                        "name": variant.get("title") or variant.get("name") or variant.get("option1"),
                        "sku": variant.get("sku") or variant.get("id"),
                        "price": self._normalize_price(variant.get("price")),
                        "priceCurrency": payload.get("currency") or payload.get("currencyCode") or "",
                        "availability": self._availability_from_flags(
                            variant.get("available"),
                            variant.get("inventory_quantity"),
                            variant.get("inventory_policy"),
                        ),
                        "inventory_quantity": variant.get("inventory_quantity"),
                    }
                )
        product["offers"] = offers
        return product

    def _extract_meta(self, html):
        return {
            match.group("name").lower(): unescape(match.group("content"))
            for match in self.meta_pattern.finditer(html)
        }

    def _extract_images(self, product_data, meta):
        images = product_data.get("image") or meta.get("og:image")
        if not images:
            return []
        if isinstance(images, str):
            return [images]
        if isinstance(images, list):
            return [self._normalize_url(image) for image in images if isinstance(image, str)]
        return []

    def _extract_price(self, product_data):
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

    def _extract_stock_status(self, product_data, html):
        offers = product_data.get("offers")
        if isinstance(offers, list) and offers:
            statuses = [self._stock_from_offer(offer) for offer in offers]
            statuses = [status for status in statuses if status != Product.StockStatus.UNKNOWN]
            if Product.StockStatus.IN_STOCK in statuses:
                return Product.StockStatus.IN_STOCK
            if statuses:
                return statuses[0]
            offers = offers[0]
        offers = offers or {}

        status = self._stock_from_offer(offers)
        if status != Product.StockStatus.UNKNOWN:
            return status

        html_lower = html.lower()
        for term, mapped_status in self.availability_terms.items():
            if term in html_lower:
                return mapped_status
        return Product.StockStatus.UNKNOWN

    def _extract_stock_quantity(self, product_data):
        offers = product_data.get("offers")
        if isinstance(offers, list):
            quantities = [
                self._coerce_int(offer.get("inventory_quantity"))
                for offer in offers
                if isinstance(offer, dict)
            ]
            quantities = [qty for qty in quantities if qty is not None]
            return sum(quantities) if quantities else None
        if isinstance(offers, dict):
            return self._coerce_int(offers.get("inventory_quantity"))
        return None

    def _extract_variants(self, product_data):
        variants = []
        for offer in product_data.get("offers", []) if isinstance(product_data.get("offers"), list) else []:
            size_label = offer.get("name") or offer.get("sku")
            if not size_label:
                continue
            variants.append(
                {
                    "name": str(size_label),
                    "source_variant_id": str(offer.get("sku") or ""),
                    "source_sku": str(offer.get("sku") or ""),
                    "stock_status": self._stock_from_offer(offer),
                    "stock_quantity": self._coerce_int(offer.get("inventory_quantity")),
                }
            )
        return variants

    def _stock_from_offer(self, offer):
        if not isinstance(offer, dict):
            return Product.StockStatus.UNKNOWN

        availability = str(offer.get("availability", "")).lower()
        if "instock" in availability:
            return Product.StockStatus.IN_STOCK
        if "outofstock" in availability or "soldout" in availability:
            return Product.StockStatus.OUT_OF_STOCK

        inventory_quantity = self._coerce_int(offer.get("inventory_quantity"))
        if inventory_quantity is not None:
            return (
                Product.StockStatus.IN_STOCK
                if inventory_quantity > 0
                else Product.StockStatus.OUT_OF_STOCK
            )

        available_flag = offer.get("available")
        if available_flag is True:
            return Product.StockStatus.IN_STOCK
        if available_flag is False:
            return Product.StockStatus.OUT_OF_STOCK
        return Product.StockStatus.UNKNOWN

    def _availability_from_flags(self, available_flag, inventory_quantity, inventory_policy):
        quantity = self._coerce_int(inventory_quantity)
        if available_flag is True or (quantity is not None and quantity > 0):
            return "https://schema.org/InStock"
        if available_flag is False and inventory_policy != "continue":
            return "https://schema.org/OutOfStock"
        return ""

    def _normalize_price(self, value):
        if value in (None, ""):
            return None
        if isinstance(value, str) and value.isdigit():
            return str(Decimal(value) / 100)
        return value

    def _coerce_int(self, value):
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _strip_html(self, value):
        if not isinstance(value, str):
            return value
        return re.sub(r"<[^>]+>", " ", unescape(value)).strip()

    def _normalize_url(self, url):
        if not isinstance(url, str):
            return url
        if url.startswith("//"):
            return f"https:{url}"
        return urljoin(self.brand.website_url, url)
