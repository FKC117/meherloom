from django.db import transaction
from django.utils import timezone

from meherloom.models import Order, Product
from meherloom.services.catalog import sync_product_from_source


@transaction.atomic
def confirm_order_with_live_stock(order):
    rejection_reasons = []

    for item in order.items.select_related("product", "variant"):
        product = sync_product_from_source(item.product, refresh_details=False)
        if product.stock_status != Product.StockStatus.IN_STOCK:
            rejection_reasons.append(f"{product.title or product.source_url} is out of stock.")
            continue

        if item.variant_id:
            variant = product.variants.filter(pk=item.variant_id).first()
            if variant and variant.stock_status != Product.StockStatus.IN_STOCK:
                rejection_reasons.append(f"{variant.name} for {product.title} is out of stock.")

    order.source_stock_checked_at = timezone.now()
    if rejection_reasons:
        order.status = Order.Status.REJECTED
        order.rejection_reason = " ".join(rejection_reasons)
    else:
        order.status = Order.Status.CONFIRMED
        order.rejection_reason = ""
    order.save(update_fields=["status", "rejection_reason", "source_stock_checked_at", "updated_at"])
    return order
