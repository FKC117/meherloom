from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meherloom", "0003_alter_brand_adapter_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="size_guide_image",
            field=models.FileField(blank=True, upload_to="size-guides/"),
        ),
    ]
