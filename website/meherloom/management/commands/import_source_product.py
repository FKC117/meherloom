from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from meherloom.models import Brand, Product
from meherloom.services.catalog import import_product_from_source


class Command(BaseCommand):
    help = "Create a product from a mother-brand product URL and import its details."

    def add_arguments(self, parser):
        parser.add_argument("brand_id", type=int)
        parser.add_argument("source_url")
        parser.add_argument("manual_price")

    def handle(self, *args, **options):
        try:
            brand = Brand.objects.get(pk=options["brand_id"])
        except Brand.DoesNotExist as exc:
            raise CommandError("Brand not found.") from exc

        product = Product.objects.create(
            brand=brand,
            source_url=options["source_url"],
            manual_price=Decimal(options["manual_price"]),
        )
        import_product_from_source(product)
        self.stdout.write(self.style.SUCCESS(f"Imported product #{product.pk}"))
