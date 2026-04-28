from django.core.management.base import BaseCommand

from meherloom.brand_seed import BRAND_SEED_DATA
from meherloom.models import Brand


class Command(BaseCommand):
    help = "Upsert researched fashion brands into the Brand model."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for item in BRAND_SEED_DATA:
            brand, created = Brand.objects.get_or_create(
                name=item["name"],
                defaults={
                    "website_url": item["website_url"],
                    "adapter_key": item["adapter_key"],
                    "notes": item["notes"],
                    "is_active": True,
                },
            )

            if created:
                created_count += 1
                continue

            changed = False
            for field in ("website_url", "adapter_key", "notes"):
                if getattr(brand, field) != item[field]:
                    setattr(brand, field, item[field])
                    changed = True

            if changed:
                brand.save(update_fields=["website_url", "adapter_key", "notes", "updated_at"])
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Brands seeded. Created: {created_count}, Updated: {updated_count}"
            )
        )
