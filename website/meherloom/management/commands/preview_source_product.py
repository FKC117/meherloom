from decimal import Decimal
from pprint import pformat

from django.core.management.base import BaseCommand, CommandError

from meherloom.models import Brand, Product
from meherloom.services.scrapers import get_adapter


class Command(BaseCommand):
    help = "Preview scraped payload for a source product URL without saving it."

    def add_arguments(self, parser):
        parser.add_argument("brand_id", type=int)
        parser.add_argument("source_url")
        parser.add_argument("manual_price", nargs="?", default="0.00")

    def handle(self, *args, **options):
        try:
            brand = Brand.objects.get(pk=options["brand_id"])
        except Brand.DoesNotExist as exc:
            raise CommandError("Brand not found.") from exc

        product = Product(
            brand=brand,
            source_url=options["source_url"],
            manual_price=Decimal(options["manual_price"]),
        )
        adapter = get_adapter(brand.adapter_key, brand=brand)
        payload = adapter.fetch_product(product)

        self.stdout.write(self.style.SUCCESS("Scrape preview:"))
        self.stdout.write(pformat(payload, sort_dicts=False))
