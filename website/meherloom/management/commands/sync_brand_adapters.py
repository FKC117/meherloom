from django.core.management.base import BaseCommand

from meherloom.models import Brand


BRAND_ADAPTER_MAP = {
    "Agha Noor": Brand.Adapter.AGHA_NOOR,
    "Limelight": Brand.Adapter.SHOPIFY,
    "Maria.B.": Brand.Adapter.SHOPIFY,
    "Sana Safinaz": Brand.Adapter.SHOPIFY,
    "SAPPHIRE": Brand.Adapter.SAPPHIRE,
}


class Command(BaseCommand):
    help = "Update known brands with the recommended scraper adapter keys."

    def handle(self, *args, **options):
        updated = 0
        missing = []

        for brand_name, adapter_key in BRAND_ADAPTER_MAP.items():
            brand = Brand.objects.filter(name=brand_name).first()
            if not brand:
                missing.append(brand_name)
                continue
            if brand.adapter_key != adapter_key:
                brand.adapter_key = adapter_key
                brand.save(update_fields=["adapter_key", "updated_at"])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} brand adapter tag(s)."))
        if missing:
            self.stdout.write(
                self.style.WARNING(f"Missing brands: {', '.join(missing)}")
            )
