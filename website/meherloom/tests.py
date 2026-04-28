from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .management.commands.sync_brand_adapters import BRAND_ADAPTER_MAP
from .models import Brand, Order, OrderItem, Product
from .services.catalog import sync_product_from_source
from .services.orders import confirm_order_with_live_stock
from .services.scrapers.generic import GenericBrandAdapter
from .services.scrapers.agha_noor import AghaNoorBrandAdapter
from .services.scrapers.sapphire import SapphireBrandAdapter
from .services.scrapers.shopify import ShopifyBrandAdapter


class CatalogSyncTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Demo Brand",
            website_url="https://example.com",
        )
        self.product = Product.objects.create(
            brand=self.brand,
            source_url="https://example.com/products/rosewood",
            manual_price=Decimal("149.00"),
        )

    @patch("meherloom.services.catalog.get_adapter")
    def test_sync_product_updates_stock_and_fields(self, mocked_get_adapter):
        class FakeAdapter:
            def fetch_product(self, product):
                return {
                    "title": "Rosewood Evening Dress",
                    "description": "Elegant silhouette",
                    "source_product_id": "123",
                    "source_sku": "SKU-123",
                    "source_currency": "USD",
                    "source_price": Decimal("89.00"),
                    "stock_status": Product.StockStatus.IN_STOCK,
                    "stock_quantity": 4,
                    "image_urls": ["https://example.com/one.jpg"],
                    "variants": [{"name": "M", "stock_status": Product.StockStatus.IN_STOCK}],
                }

        mocked_get_adapter.return_value = FakeAdapter()

        sync_product_from_source(self.product, refresh_details=True)
        self.product.refresh_from_db()

        self.assertEqual(self.product.title, "Rosewood Evening Dress")
        self.assertEqual(self.product.stock_status, Product.StockStatus.IN_STOCK)
        self.assertEqual(self.product.images.count(), 1)
        self.assertEqual(self.product.variants.count(), 1)


class OrderConfirmationTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Demo Brand",
            website_url="https://example.com",
        )
        self.product = Product.objects.create(
            brand=self.brand,
            source_url="https://example.com/products/rosewood",
            title="Rosewood Evening Dress",
            manual_price=Decimal("149.00"),
            stock_status=Product.StockStatus.UNKNOWN,
        )

    @patch("meherloom.services.orders.sync_product_from_source")
    def test_order_confirms_when_product_is_in_stock(self, mocked_sync):
        def fake_sync(product, refresh_details=False):
            product.stock_status = Product.StockStatus.IN_STOCK
            product.save(update_fields=["stock_status"])
            return product

        mocked_sync.side_effect = fake_sync

        order = Order.objects.create(customer_name="Nadia", customer_email="nadia@example.com")
        OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            unit_price=self.product.manual_price,
        )

        confirm_order_with_live_stock(order)
        order.refresh_from_db()

        self.assertEqual(order.status, Order.Status.CONFIRMED)

    @patch("meherloom.services.orders.sync_product_from_source")
    def test_order_rejects_when_product_is_out_of_stock(self, mocked_sync):
        def fake_sync(product, refresh_details=False):
            product.stock_status = Product.StockStatus.OUT_OF_STOCK
            product.save(update_fields=["stock_status"])
            return product

        mocked_sync.side_effect = fake_sync

        order = Order.objects.create(customer_name="Nadia", customer_email="nadia@example.com")
        OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            unit_price=self.product.manual_price,
        )

        confirm_order_with_live_stock(order)
        order.refresh_from_db()

        self.assertEqual(order.status, Order.Status.REJECTED)


class GenericScraperTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Demo Brand",
            website_url="https://example.com",
        )

    def test_generic_scraper_reads_json_ld_product(self):
        adapter = GenericBrandAdapter(brand=self.brand)
        html = """
        <html>
            <head>
                <script type="application/ld+json">
                {
                    "@context": "https://schema.org",
                    "@type": "Product",
                    "name": "Rosewood Dress",
                    "description": "Elegant eventwear",
                    "image": ["https://example.com/dress.jpg"],
                    "sku": "RW-1",
                    "offers": {
                        "@type": "Offer",
                        "price": "99.00",
                        "priceCurrency": "USD",
                        "availability": "https://schema.org/InStock"
                    }
                }
                </script>
            </head>
        </html>
        """

        payload = adapter._extract_product_from_json_ld(html)
        self.assertEqual(payload["name"], "Rosewood Dress")
        self.assertEqual(adapter._extract_stock_status(payload, html), Product.StockStatus.IN_STOCK)

    def test_generic_scraper_reads_shopify_like_embedded_json(self):
        adapter = GenericBrandAdapter(brand=self.brand)
        html = """
        <html>
            <script>
                var meta = {
                    "product": {
                        "id": 10,
                        "title": "Ivory Bloom",
                        "body_html": "<p>Soft pleated dress</p>",
                        "images": [{"src": "//cdn.example.com/ivory.jpg"}],
                        "variants": [
                            {
                                "id": 201,
                                "title": "M",
                                "sku": "IV-M",
                                "price": "12900",
                                "available": true,
                                "inventory_quantity": 3
                            }
                        ]
                    }
                };
            </script>
        </html>
        """

        payload = adapter._extract_product_from_embedded_json(html)
        self.assertEqual(payload["name"], "Ivory Bloom")
        self.assertEqual(payload["offers"][0]["sku"], "IV-M")
        self.assertEqual(adapter._extract_stock_quantity(payload), 3)


class ShopifyScraperTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Shopify Brand",
            website_url="https://example.com",
            adapter_key=Brand.Adapter.SHOPIFY,
        )

    def test_build_product_json_url_from_collection_product_url(self):
        adapter = ShopifyBrandAdapter(brand=self.brand)
        url = adapter._build_product_json_url(
            "https://example.com/collections/sale/products/rosewood-dress"
        )
        self.assertEqual(url, "https://example.com/products/rosewood-dress.js")

    def test_shopify_adapter_uses_product_json_variant_stock(self):
        adapter = ShopifyBrandAdapter(brand=self.brand)

        def fake_fetch_url(url):
            self.assertEqual(url, "https://example.com/products/rosewood-dress.js")
            return """
            {
                "id": 1001,
                "title": "Rosewood Dress",
                "description": "<p>Elegant eventwear</p>",
                "images": ["//cdn.example.com/rosewood.jpg"],
                "variants": [
                    {
                        "id": 11,
                        "title": "XS / Peach",
                        "sku": "RW-XS",
                        "available": false,
                        "price": 14900
                    },
                    {
                        "id": 12,
                        "title": "S / Peach",
                        "sku": "RW-S",
                        "available": true,
                        "price": 14900
                    }
                ]
            }
            """

        adapter.fetch_url = fake_fetch_url
        product = Product(
            brand=self.brand,
            source_url="https://example.com/products/rosewood-dress",
            manual_price=Decimal("149.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["title"], "Rosewood Dress")
        self.assertEqual(payload["stock_status"], Product.StockStatus.IN_STOCK)
        self.assertEqual(payload["source_price"], Decimal("149"))
        self.assertEqual(payload["variants"][1]["source_sku"], "RW-S")
        self.assertEqual(payload["image_urls"][0], "https://cdn.example.com/rosewood.jpg")


class SapphireScraperTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="SAPPHIRE",
            website_url="https://pk.sapphireonline.pk/",
            adapter_key=Brand.Adapter.SAPPHIRE,
        )

    def test_sapphire_adapter_detects_in_stock_page_with_add_to_bag(self):
        adapter = SapphireBrandAdapter(brand=self.brand)
        html = """
        <html>
            <head>
                <meta property="og:title" content="Printed Cambric Culottes" />
            </head>
            <body>
                <h1>Printed Cambric Culottes</h1>
                <div>Rs.2,290</div>
                <div>SKU: S26CAHMV111T_999</div>
                <div>Select your size</div>
                <button>XS</button><button>S</button><button>M</button>
                <button>Add to Bag</button>
                <div>sale starts in</div>
            </body>
        </html>
        """

        adapter.fetch_url = lambda url: html
        product = Product(
            brand=self.brand,
            source_url="https://pk.sapphireonline.pk/collections/ready-to-wear/products/S26CAHMV111T_999.html",
            manual_price=Decimal("2290.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["title"], "Printed Cambric Culottes")
        self.assertEqual(payload["source_sku"], "S26CAHMV111T_999")
        self.assertEqual(payload["source_price"], Decimal("2290"))
        self.assertEqual(payload["stock_status"], Product.StockStatus.IN_STOCK)
        self.assertEqual(len(payload["variants"]), 3)

    def test_sapphire_adapter_detects_out_of_stock_notify_state(self):
        adapter = SapphireBrandAdapter(brand=self.brand)
        html = """
        <html>
            <body>
                <h1>3 Piece Embroidered Lawn Suit</h1>
                <div>Rs.5,313</div>
                <div>SKU: 0U3PDY25V618</div>
                <div>Notify me when available</div>
            </body>
        </html>
        """

        adapter.fetch_url = lambda url: html
        product = Product(
            brand=self.brand,
            source_url="https://pk.sapphireonline.pk/collections/three-piece-unstitched/products/0U3PDY25V618.html",
            manual_price=Decimal("5313.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["stock_status"], Product.StockStatus.OUT_OF_STOCK)

    def test_sapphire_adapter_prefers_html_title_sku_and_images_over_noisy_embedded_data(self):
        adapter = SapphireBrandAdapter(brand=self.brand)
        html = """
        <html>
            <head>
                <meta property="og:title" content="Unstitched 3 PC FELXS26V126B Sapphire PK" />
            </head>
            <body>
                <script>{"contextSecondaryAUIDs":"bad-data"}</script>
                <h1>3 Piece - Embroidered Raw Silk Suit</h1>
                <div>Rs.7,990</div>
                <div>SKU: U3FELXS26V14</div>
                <img src="https://pk.sapphireonline.pk/cdn/shop/files/look-1.jpg?v=1">
                <img src="https://pk.sapphireonline.pk/cdn/shop/files/look-2.jpg?v=1">
                <button>Add to Bag</button>
                <button>Details</button>
                <button>Description</button>
                <div>Unstitched 3-Piece Plum ensemble with embroidered shirt, dupatta and trouser.</div>
                <div>Share this Look</div>
            </body>
        </html>
        """

        adapter.fetch_url = lambda url: html
        product = Product(
            brand=self.brand,
            source_url="https://pk.sapphireonline.pk/collections/unstitched/products/U3FELXS26V14.html",
            manual_price=Decimal("7990.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["title"], "3 Piece - Embroidered Raw Silk Suit")
        self.assertEqual(payload["source_sku"], "U3FELXS26V14")
        self.assertEqual(len(payload["image_urls"]), 2)

    def test_sapphire_adapter_handles_realistic_noisy_page_text(self):
        adapter = SapphireBrandAdapter(brand=self.brand)
        html = """
        <html>
            <body>
                <script>{"contextSecondaryAUIDs":"bad-data"}</script>
                <div>Home Woman Unstitched 3 Piece - Printed Lawn Suit Rs.3,990 SKU: U3PDDS26V443 Notify Me Add to Bag sale starts in You'll be able to add products to cart once the timer hits 0! Details Description Unstitched 3-Piece Shirt Printed Lawn Shirt 3m Fabric: Lawn Colour: Black &amp; Off White Dupatta Printed Voile Dupatta 2.5m Fabric: Voile Colour: Black &amp; Off White Trouser Dyed Cotton Trouser 2.5m Fabric: Cotton Colour: Black Make a sophisticated statement with our three-piece printed black and off white ensemble featuring a lawn shirt paired with cotton trousers and a voile dupatta. Note: Actual product color may vary slightly from the image. Share this Look</div>
            </body>
        </html>
        """

        adapter.fetch_url = lambda url: html
        product = Product(
            brand=self.brand,
            source_url="https://pk.sapphireonline.pk/collections/unstitched/products/U3PDDS26V443.html",
            manual_price=Decimal("3990.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["title"], "3 Piece - Printed Lawn Suit")
        self.assertEqual(payload["source_sku"], "U3PDDS26V443")
        self.assertEqual(payload["source_product_id"], "U3PDDS26V443")
        self.assertIn("Make a sophisticated statement", payload["description"])

    def test_sapphire_adapter_dedupes_resized_image_variants(self):
        adapter = SapphireBrandAdapter(brand=self.brand)
        html = """
        <html>
            <body>
                <img src="https://pk.sapphireonline.pk/dw/image/v2/BKSB_PRD/on/demandware.static/-/Sites-sapphire-master-catalog/default/foo/U3PDDS26V443_2.JPG">
                <img src="https://pk.sapphireonline.pk/dw/image/v2/BKSB_PRD/on/demandware.static/-/Sites-sapphire-master-catalog/default/foo/U3PDDS26V443_2.JPG?sw=1000&amp;sh=1200">
                <img src="https://pk.sapphireonline.pk/dw/image/v2/BKSB_PRD/on/demandware.static/-/Sites-sapphire-master-catalog/default/foo/U3PDDS26V443_3.JPG">
            </body>
        </html>
        """

        images = adapter._extract_sapphire_images({}, {}, html)
        self.assertEqual(len(images), 2)


class AghaNoorScraperTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Agha Noor",
            website_url="https://pk.aghanoorofficial.com/",
            adapter_key=Brand.Adapter.AGHA_NOOR,
        )

    def test_agha_noor_adapter_extracts_variants_and_stock(self):
        adapter = AghaNoorBrandAdapter(brand=self.brand)
        html = """
        <html>
            <head>
                <meta property="og:title" content="2 Piece - Embroidered Cambric Suit S114219" />
            </head>
            <body>
                <h1>2 Piece - Embroidered Cambric Suit S114219</h1>
                <div>Rs.8,000.00</div>
                <div>Size: MEDIUM <input /> X-SMALL <input /> SMALL <input /> MEDIUM <input /> LARGE <input /> X-LARGE</div>
                <div>Color: Beige <input /> Beige</div>
                <div>Product variants</div>
                <button>Add to cart</button>
                <div>Description</div>
            </body>
        </html>
        """

        adapter.fetch_url = lambda url: html
        product = Product(
            brand=self.brand,
            source_url="https://pk.aghanoorofficial.com/products/2-piece-embroidered-cambric-suit-s114219",
            manual_price=Decimal("8000.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["title"], "2 Piece - Embroidered Cambric Suit S114219")
        self.assertEqual(payload["source_sku"], "S114219")
        self.assertEqual(payload["source_price"], Decimal("8000.00"))
        self.assertEqual(payload["stock_status"], Product.StockStatus.IN_STOCK)
        self.assertIn("MEDIUM / Beige", [variant["name"] for variant in payload["variants"]])

    def test_agha_noor_adapter_detects_sold_out_state(self):
        adapter = AghaNoorBrandAdapter(brand=self.brand)
        html = """
        <html>
            <body>
                <h1>Raw Silk Pants PNT0392</h1>
                <div>Rs.3,500.00</div>
                <div>Leave your email and we will notify as soon as the product / variant is back in stock</div>
                <button>Sold out</button>
            </body>
        </html>
        """

        adapter.fetch_url = lambda url: html
        product = Product(
            brand=self.brand,
            source_url="https://pk.aghanoorofficial.com/products/embroidered-lawn-pants-pnt0392",
            manual_price=Decimal("3500.00"),
        )

        payload = adapter.fetch_product(product)

        self.assertEqual(payload["stock_status"], Product.StockStatus.OUT_OF_STOCK)


class StorefrontViewTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Store Brand",
            website_url="https://example.com",
        )
        self.product = Product.objects.create(
            brand=self.brand,
            source_url="https://example.com/products/store-dress",
            title="Store Dress",
            description="Imported copy for storefront display.",
            source_sku="STORE-01",
            manual_price=Decimal("199.00"),
            stock_status=Product.StockStatus.IN_STOCK,
            sync_status=Product.SyncStatus.ACTIVE,
            is_published=True,
        )
        self.product.variants.create(
            name="M / Beige",
            source_sku="STORE-01-M",
            stock_status=Product.StockStatus.IN_STOCK,
        )
        self.product.variants.create(
            name="L / Beige",
            source_sku="STORE-01-L",
            stock_status=Product.StockStatus.OUT_OF_STOCK,
        )

    def test_index_renders_database_products(self):
        response = self.client.get(reverse("meherloom:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Store Dress")
        self.assertContains(response, "Store Brand")

    def test_product_detail_renders_database_product(self):
        response = self.client.get(reverse("meherloom:product_detail", args=[self.product.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Store Dress")
        self.assertContains(response, "STORE-01")
        self.assertContains(response, "M / Beige")
        self.assertContains(response, "Origin brand")
        self.assertContains(response, "In stock")
        self.assertContains(response, "L / Beige")

    def test_shop_page_renders_database_products(self):
        response = self.client.get(reverse("meherloom:shop"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imported catalog, ready for preorder.")
        self.assertContains(response, "Store Dress")
        self.assertContains(response, "M / Beige")


class AdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.client.login(username="admin", password="password123")

    def test_brand_action_shows_feedback_when_brand_already_mapped(self):
        brand = Brand.objects.create(
            name="Maria.B.",
            website_url="https://www.mariabbd.com/",
            adapter_key=BRAND_ADAPTER_MAP["Maria.B."],
        )

        response = self.client.post(
            reverse("admin:meherloom_brand_changelist"),
            {
                "action": "apply_recommended_scrapers",
                "_selected_action": [brand.pk],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = list(response.context["messages"])
        self.assertTrue(any("already correct" in str(message) for message in messages))


class ImportProductViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff_user = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="password123",
            is_staff=True,
        )
        self.brand = Brand.objects.create(
            name="Maria.B.",
            website_url="https://www.mariabbd.com/",
            adapter_key=Brand.Adapter.SHOPIFY,
            is_active=True,
        )

    def test_staff_can_open_import_page(self):
        self.client.login(username="staff", password="password123")
        response = self.client.get(reverse("meherloom:import_product"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import a source product into your catalog.")

    @patch("meherloom.views.import_product_from_source")
    def test_staff_import_form_creates_product_and_redirects(self, mocked_import):
        def fake_import(product):
            product.title = "Imported Maria.B. Dress"
            product.sync_status = Product.SyncStatus.ACTIVE
            product.stock_status = Product.StockStatus.IN_STOCK
            product.save(update_fields=["title", "sync_status", "stock_status"])
            product.variants.create(
                name="S",
                source_sku="SKU-S",
                stock_status=Product.StockStatus.IN_STOCK,
            )
            return product

        mocked_import.side_effect = fake_import
        self.client.login(username="staff", password="password123")

        response = self.client.post(
            reverse("meherloom:import_product"),
            {
                "brand": self.brand.pk,
                "source_url": "https://www.mariabbd.com/products/dw-ef26-62-off-white",
                "manual_price": "149.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(title="Imported Maria.B. Dress").exists())
        self.assertContains(response, "Imported Maria.B. Dress")
        self.assertContains(response, "S")
