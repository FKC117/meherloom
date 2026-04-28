from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify

from meherloom.models import Product, ProductImage, ProductVariant
from meherloom.services.scrapers import get_adapter


def import_product_from_source(product):
    return sync_product_from_source(product, refresh_details=True)


@transaction.atomic
def sync_product_from_source(product, refresh_details=False):
    adapter = get_adapter(product.brand.adapter_key, brand=product.brand)
    payload = adapter.fetch_product(product)

    if refresh_details:
        product.title = payload.get("title", product.title)
        product.slug = slugify(product.title)[:255]
        product.description = payload.get("description", product.description)
        product.source_product_id = payload.get("source_product_id", product.source_product_id)
        product.source_sku = payload.get("source_sku", product.source_sku)
        product.source_currency = payload.get("source_currency", product.source_currency)
        if payload.get("source_price") is not None:
            product.source_price = payload["source_price"]

    product.stock_status = payload.get("stock_status", Product.StockStatus.UNKNOWN)
    product.stock_quantity = payload.get("stock_quantity")
    product.sync_error = ""
    product.sync_status = Product.SyncStatus.ACTIVE
    now = timezone.now()
    product.last_checked_at = now
    if refresh_details:
        product.last_imported_at = now
    product.schedule_next_check()
    product.save()

    if refresh_details and "image_urls" in payload:
        ProductImage.objects.filter(product=product).delete()
        ProductImage.objects.bulk_create(
            [
                ProductImage(
                    product=product,
                    image_url=image_url,
                    sort_order=index,
                )
                for index, image_url in enumerate(payload["image_urls"])
            ]
        )

    if "variants" in payload:
        ProductVariant.objects.filter(product=product).delete()
        ProductVariant.objects.bulk_create(
            [
                ProductVariant(
                    product=product,
                    name=variant["name"],
                    source_variant_id=variant.get("source_variant_id", ""),
                    source_sku=variant.get("source_sku", ""),
                    stock_status=variant.get("stock_status", Product.StockStatus.UNKNOWN),
                    stock_quantity=variant.get("stock_quantity"),
                )
                for variant in payload["variants"]
                if variant.get("name")
            ]
        )

    return product


def sync_due_products(queryset=None, refresh_details=False):
    now = timezone.now()
    if queryset is None:
        queryset = Product.objects.filter(
            sync_status__in=[Product.SyncStatus.DRAFT, Product.SyncStatus.ACTIVE],
            brand__is_active=True,
            is_published=True,
        ).filter(models.Q(next_check_at__isnull=True) | models.Q(next_check_at__lte=now))
    else:
        queryset = queryset.filter(brand__is_active=True)

    synced = []
    errors = []
    for product in queryset.select_related("brand"):
        try:
            synced.append(sync_product_from_source(product, refresh_details=refresh_details))
        except Exception as exc:
            product.sync_status = Product.SyncStatus.ERROR
            product.sync_error = str(exc)
            product.last_checked_at = now
            product.schedule_next_check()
            product.save(update_fields=["sync_status", "sync_error", "last_checked_at", "next_check_at"])
            errors.append((product.pk, str(exc)))
    return synced, errors
