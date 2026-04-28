from django.core.management.base import BaseCommand

from meherloom.models import Product
from meherloom.services.catalog import sync_due_products


class Command(BaseCommand):
    help = "Refresh stock from mother-brand product pages. Safe to run from cPanel cron every 5 minutes."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int)
        parser.add_argument("--refresh-details", action="store_true")

    def handle(self, *args, **options):
        queryset = Product.objects.all()
        if options.get("product_id"):
            queryset = queryset.filter(pk=options["product_id"])

        synced, errors = sync_due_products(
            queryset=queryset,
            refresh_details=options["refresh_details"],
        )
        self.stdout.write(self.style.SUCCESS(f"Synced {len(synced)} product(s)."))
        if errors:
            for product_id, error in errors:
                self.stdout.write(self.style.WARNING(f"Product {product_id}: {error}"))
