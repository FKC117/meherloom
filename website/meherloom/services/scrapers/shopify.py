from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from meherloom.models import Product
from meherloom.services.scrapers.generic import GenericBrandAdapter


class ShopifyBrandAdapter(GenericBrandAdapter):
    def fetch_product(self, product):
        product_json = self._fetch_product_json(product.source_url)
        if product_json:
            return self._payload_from_product_json(product_json, product.source_url)
        return super().fetch_product(product)

    def _fetch_product_json(self, source_url):
        product_json_url = self._build_product_json_url(source_url)
        raw_json = self.fetch_url(product_json_url)
        return self.parse_json_candidate(raw_json)

    def _build_product_json_url(self, source_url):
        parsed = urlparse(source_url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if "products" not in path_parts:
            raise ValueError(f"Could not determine Shopify product handle from URL: {source_url}")

        handle_index = path_parts.index("products") + 1
        if handle_index >= len(path_parts):
            raise ValueError(f"Missing Shopify product handle in URL: {source_url}")

        handle = path_parts[handle_index]
        return f"{parsed.scheme}://{parsed.netloc}/products/{handle}.js"

    def _payload_from_product_json(self, payload, source_url):
        variants = payload.get("variants") or []
        normalized_variants = []
        source_price = None
        source_currency = str(payload.get("currency") or "").upper()

        for variant in variants:
            if not isinstance(variant, dict):
                continue
            normalized_price = self._normalize_variant_price(variant.get("price"))
            if source_price is None and normalized_price is not None:
                source_price = normalized_price
            normalized_variants.append(
                {
                    "name": self._variant_name(variant),
                    "source_variant_id": str(variant.get("id") or ""),
                    "source_sku": str(variant.get("sku") or ""),
                    "stock_status": (
                        Product.StockStatus.IN_STOCK
                        if variant.get("available")
                        else Product.StockStatus.OUT_OF_STOCK
                    ),
                    "stock_quantity": self._coerce_int(variant.get("inventory_quantity")),
                }
            )

        image_urls = []
        for image in payload.get("images") or []:
            if isinstance(image, str):
                image_urls.append(self._normalize_url(image))

        stock_status = Product.StockStatus.OUT_OF_STOCK
        if not normalized_variants:
            stock_status = Product.StockStatus.UNKNOWN
        elif any(variant["stock_status"] == Product.StockStatus.IN_STOCK for variant in normalized_variants):
            stock_status = Product.StockStatus.IN_STOCK

        quantities = [
            variant["stock_quantity"]
            for variant in normalized_variants
            if variant.get("stock_quantity") is not None
        ]

        first_variant = next((variant for variant in variants if isinstance(variant, dict)), {})

        return {
            "title": str(payload.get("title") or "").strip(),
            "description": self._strip_html(payload.get("description") or payload.get("body_html") or ""),
            "source_product_id": str(payload.get("id") or ""),
            "source_sku": str(first_variant.get("sku") or ""),
            "source_currency": source_currency,
            "source_price": source_price,
            "stock_status": stock_status,
            "stock_quantity": sum(quantities) if quantities else None,
            "image_urls": image_urls,
            "variants": normalized_variants,
            "source_url": source_url,
        }

    def _variant_name(self, variant):
        title = str(variant.get("title") or "").strip()
        if title and title.lower() != "default title":
            return title

        option_values = []
        for option_key in ("option1", "option2", "option3"):
            option_value = variant.get(option_key)
            if option_value:
                option_values.append(str(option_value).strip())
        if option_values:
            return " / ".join(option_values)

        sku = variant.get("sku")
        if sku:
            return str(sku)
        return str(variant.get("id") or "Default")

    def _normalize_variant_price(self, value):
        if value in (None, ""):
            return None
        try:
            raw = Decimal(str(value))
        except InvalidOperation:
            return None

        # Shopify product.js often returns prices in minor units for many stores.
        if raw == raw.to_integral_value() and raw >= 1000:
            raw = raw / Decimal("100")
        return raw
