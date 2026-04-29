from django.contrib import admin, messages
from django.db.models import Count
from django.db import models
from django.forms import Textarea, TextInput

from .models import Brand, Order, OrderItem, Product, ProductImage, ProductVariant
from .management.commands.sync_brand_adapters import BRAND_ADAPTER_MAP
from .services.catalog import import_product_from_source, sync_product_from_source


def _summarize_errors(error_map):
    if not error_map:
        return ""
    sample = "; ".join(error_map[:3])
    return f" Errors: {sample}"


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ("image_url", "alt_text", "sort_order")


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ("name", "source_variant_id", "source_sku", "stock_status", "stock_quantity")


@admin.action(description="Apply recommended scraper types to selected brands")
def apply_recommended_scrapers(modeladmin, request, queryset):
    updated = 0
    skipped = 0
    for brand in queryset:
        recommended = BRAND_ADAPTER_MAP.get(brand.name)
        if not recommended:
            skipped += 1
            continue
        if brand.adapter_key != recommended:
            brand.adapter_key = recommended
            brand.save(update_fields=["adapter_key", "updated_at"])
            updated += 1
        else:
            skipped += 1

    if updated:
        messages.success(request, f"Updated scraper type for {updated} brand(s).")
    if skipped:
        messages.info(request, f"{skipped} selected brand(s) were already correct or have no mapping.")
    if not updated and not skipped:
        messages.warning(request, "No brands were selected.")


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "adapter_key",
        "product_count",
        "is_active",
        "check_every_minutes",
        "request_timeout",
    )
    list_filter = ("is_active", "adapter_key")
    search_fields = ("name", "website_url", "notes")
    actions = [apply_recommended_scrapers]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(product_total=Count("products"))

    @admin.display(ordering="product_total", description="Products")
    def product_count(self, obj):
        return obj.product_total


@admin.action(description="Refresh selected products from source websites")
def refresh_products(modeladmin, request, queryset):
    synced = 0
    failed = 0
    errors = []
    for product in queryset:
        try:
            sync_product_from_source(product, refresh_details=True)
            synced += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{product.pk}: {exc}")
    if synced:
        messages.success(request, f"Refreshed {synced} product(s).")
    if failed:
        messages.warning(request, f"{failed} product(s) failed to refresh.{_summarize_errors(errors)}")
    if not synced and not failed:
        messages.info(request, "No products were selected.")


@admin.action(description="Attempt initial import for selected draft products")
def import_selected_products(modeladmin, request, queryset):
    imported = 0
    failed = 0
    errors = []
    for product in queryset:
        try:
            import_product_from_source(product)
            imported += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{product.pk}: {exc}")
    if imported:
        messages.success(request, f"Imported {imported} product(s) from source URLs.")
    if failed:
        messages.warning(request, f"{failed} product(s) failed to import.{_summarize_errors(errors)}")
    if not imported and not failed:
        messages.info(request, "No products were selected.")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "brand",
        "manual_price",
        "source_price",
        "source_sku",
        "stock_status",
        "sync_status",
        "last_checked_at",
        "is_published",
    )
    list_filter = ("brand", "stock_status", "sync_status", "is_published")
    list_editable = ("manual_price",)
    list_display_links = ("id", "title")   
    search_fields = ("title", "source_url", "source_sku", "source_product_id")
    actions = [import_selected_products, refresh_products]
    inlines = [ProductImageInline, ProductVariantInline]
    autocomplete_fields = ("brand",)
    formfield_overrides = {
        models.TextField: {"widget": Textarea(attrs={"rows": 6})},
        models.CharField: {"widget": TextInput(attrs={"size": 60})},
    }
    readonly_fields = (
        "slug",
        "last_checked_at",
        "last_imported_at",
        "next_check_at",
        "sync_status",
        "sync_error",
    )
    fieldsets = (
        (
            "Catalog",
            {
                "fields": (
                    "brand",
                    "source_url",
                    "is_published",
                )
            },
        ),
        (
            "Storefront",
            {
                "fields": (
                    "title",
                    "manual_price",
                    "slug",
                )
            },
        ),
        (
            "Imported Details",
            {
                "fields": (
                    "description",
                    "size_guide",
                    "size_guide_image",
                ),
                "description": "This is where the imported product details are stored for your storefront. You can edit them manually.",
            },
        ),
        (
            "Source Data",
            {
                "fields": (
                    "source_product_id",
                    "source_sku",
                    "source_currency",
                    "source_price",
                    "stock_status",
                    "stock_quantity",
                )
            },
        ),
        (
            "Sync Status",
            {
                "fields": (
                    "last_checked_at",
                    "last_imported_at",
                    "next_check_at",
                    "sync_status",
                    "sync_error",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and obj.source_url:
            try:
                import_product_from_source(obj)
                self.message_user(request, "Product imported from source URL.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(
                    request,
                    f"Product saved, but source import failed: {exc}",
                    messages.WARNING,
                )


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ("product", "variant")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer_name", "customer_email", "status", "source_stock_checked_at", "created_at")
    list_filter = ("status",)
    search_fields = ("customer_name", "customer_email")
    inlines = [OrderItemInline]


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("product", "sort_order", "image_url")
    list_filter = ("product__brand",)
    search_fields = ("product__title", "image_url", "alt_text")
    autocomplete_fields = ("product",)


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("name", "product", "stock_status", "stock_quantity", "source_sku")
    list_filter = ("stock_status", "product__brand")
    search_fields = ("name", "product__title", "source_variant_id", "source_sku")
    autocomplete_fields = ("product",)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "variant", "quantity", "unit_price")
    list_filter = ("order__status", "product__brand")
    search_fields = ("order__customer_name", "product__title", "variant__name")
    autocomplete_fields = ("order", "product", "variant")
