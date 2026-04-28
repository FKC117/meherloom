from datetime import timedelta
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Brand(TimeStampedModel):
    class Adapter(models.TextChoices):
        GENERIC = "generic", "Generic JSON-LD scraper"
        SHOPIFY = "shopify", "Shopify product scraper"
        SAPPHIRE = "sapphire", "SAPPHIRE product scraper"
        AGHA_NOOR = "agha_noor", "Agha Noor product scraper"

    name = models.CharField(max_length=255)
    website_url = models.URLField()
    adapter_key = models.CharField(
        max_length=50,
        choices=Adapter.choices,
        default=Adapter.GENERIC,
    )
    is_active = models.BooleanField(default=True)
    check_every_minutes = models.PositiveIntegerField(default=5)
    request_timeout = models.PositiveIntegerField(default=20)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    class SyncStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ERROR = "error", "Sync error"
        ARCHIVED = "archived", "Archived"

    class StockStatus(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        OUT_OF_STOCK = "out_of_stock", "Out of stock"
        UNKNOWN = "unknown", "Unknown"

    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="products",
    )
    source_url = models.URLField(unique=True)
    source_product_id = models.CharField(max_length=255, blank=True)
    source_sku = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    size_guide = models.TextField(blank=True)
    size_guide_image = models.FileField(upload_to="size-guides/", blank=True)
    source_currency = models.CharField(max_length=10, blank=True)
    source_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    manual_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    stock_status = models.CharField(
        max_length=20,
        choices=StockStatus.choices,
        default=StockStatus.UNKNOWN,
    )
    stock_quantity = models.PositiveIntegerField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.DRAFT,
    )
    sync_error = models.TextField(blank=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or self.source_url

    def schedule_next_check(self):
        self.next_check_at = timezone.now() + timedelta(minutes=self.brand.check_every_minutes)

    @property
    def can_accept_preorder(self):
        return self.stock_status == self.StockStatus.IN_STOCK and self.is_published


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image_url = models.URLField()
    alt_text = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product} image {self.sort_order}"


class ProductVariant(TimeStampedModel):
    class StockStatus(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        OUT_OF_STOCK = "out_of_stock", "Out of stock"
        UNKNOWN = "unknown", "Unknown"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    name = models.CharField(max_length=255)
    source_variant_id = models.CharField(max_length=255, blank=True)
    source_sku = models.CharField(max_length=255, blank=True)
    stock_status = models.CharField(
        max_length=20,
        choices=StockStatus.choices,
        default=StockStatus.UNKNOWN,
    )
    stock_quantity = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("product", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.product} - {self.name}"


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    customer_name = models.CharField(max_length=255)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=50, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    rejection_reason = models.TextField(blank=True)
    source_stock_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} - {self.customer_name}"

    @property
    def total_amount(self):
        return sum(item.line_total for item in self.items.all())


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="order_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product} x {self.quantity}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity
