from decimal import Decimal, InvalidOperation

from django import forms

from .models import Brand


class ProductImportForm(forms.Form):
    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(is_active=True).order_by("name"),
        empty_label="Select a brand",
    )
    source_url = forms.URLField(label="Source product URL")
    manual_price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Your selling price",
    )

    def clean_manual_price(self):
        value = self.cleaned_data["manual_price"]
        try:
            return Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise forms.ValidationError("Enter a valid price.") from exc
