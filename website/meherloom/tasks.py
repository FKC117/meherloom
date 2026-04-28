try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(*args, **kwargs):
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]

        def decorator(func):
            return func

        return decorator

from meherloom.models import Product
from meherloom.services.catalog import sync_due_products


@shared_task
def sync_source_stock_task(refresh_details=False):
    synced, errors = sync_due_products(refresh_details=refresh_details)
    return {
        "synced_product_ids": [product.pk for product in synced],
        "errors": errors,
    }


@shared_task
def sync_single_product_task(product_id, refresh_details=False):
    synced, errors = sync_due_products(
        queryset=Product.objects.filter(pk=product_id),
        refresh_details=refresh_details,
    )
    return {
        "synced_product_ids": [product.pk for product in synced],
        "errors": errors,
    }
