from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("meherloom", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="size_guide",
            field=models.TextField(blank=True),
        ),
    ]
