from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
import re

from .forms import ProductImportForm
from .models import Brand, Product
from .services.catalog import import_product_from_source


def index(request):
    featured_products = (
        Product.objects.filter(is_published=True, sync_status=Product.SyncStatus.ACTIVE)
        .select_related("brand")
        .prefetch_related("images", "variants")
        .order_by("-updated_at")[:8]
    )
    active_brands = Brand.objects.filter(is_active=True).order_by("name")[:8]
    benefits = [
        "Source-synced stock checking",
        "Manual selling price control",
        "Preorder-ready catalog workflow",
    ]
    context = {
        "featured_products": featured_products,
        "active_brands": active_brands,
        "benefits": benefits,
    }
    return render(request, "meherloom/index.html", context)


def shop(request):
    products = (
        Product.objects.filter(is_published=True)
        .select_related("brand")
        .prefetch_related("images", "variants")
    )
    active_brands = Brand.objects.filter(is_active=True).order_by("name")

    query = request.GET.get("q", "").strip()
    brand_value = request.GET.get("brand", "").strip()
    stock_value = request.GET.get("stock", "").strip()
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()

    if query:
        products = products.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(source_sku__icontains=query)
            | Q(brand__name__icontains=query)
        )

    if brand_value.isdigit():
        products = products.filter(brand_id=int(brand_value))

    valid_stock_values = {
        Product.StockStatus.IN_STOCK,
        Product.StockStatus.OUT_OF_STOCK,
        Product.StockStatus.UNKNOWN,
    }
    if stock_value in valid_stock_values:
        products = products.filter(stock_status=stock_value)

    min_price_value = _parse_decimal(min_price)
    if min_price_value is not None:
        products = products.filter(manual_price__gte=min_price_value)

    max_price_value = _parse_decimal(max_price)
    if max_price_value is not None:
        products = products.filter(manual_price__lte=max_price_value)

    products = products.order_by("-updated_at")
    active_filter_count = sum(
        1
        for value in (
            query,
            brand_value if brand_value.isdigit() else "",
            stock_value if stock_value in valid_stock_values else "",
            min_price if min_price_value is not None else "",
            max_price if max_price_value is not None else "",
        )
        if value
    )

    context = {
        "products": products,
        "active_brands": active_brands,
        "search_query": query,
        "selected_brand": brand_value,
        "selected_stock": stock_value,
        "min_price": min_price,
        "max_price": max_price,
        "stock_choices": (
            (Product.StockStatus.IN_STOCK, "In stock"),
            (Product.StockStatus.OUT_OF_STOCK, "Out of stock"),
            (Product.StockStatus.UNKNOWN, "Stock unknown"),
        ),
        "active_filter_count": active_filter_count,
    }
    return render(request, "meherloom/shop.html", context)


@staff_member_required
def import_product(request):
    form = ProductImportForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        product = Product.objects.create(
            brand=form.cleaned_data["brand"],
            source_url=form.cleaned_data["source_url"],
            manual_price=form.cleaned_data["manual_price"],
        )
        try:
            import_product_from_source(product)
        except Exception as exc:
            product.sync_status = Product.SyncStatus.ERROR
            product.sync_error = str(exc)
            product.save(update_fields=["sync_status", "sync_error", "updated_at"])
            messages.error(request, f"Import failed: {exc}")
        else:
            messages.success(request, f"Imported {product.title or product.source_url} successfully.")
            return redirect("meherloom:product_detail", pk=product.pk)

    context = {
        "form": form,
        "recent_products": Product.objects.select_related("brand").prefetch_related("variants").order_by("-created_at")[:8],
    }
    return render(request, "meherloom/import_product.html", context)


def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related("brand").prefetch_related("images", "variants"),
        pk=pk,
        is_published=True,
    )
    related_products = (
        Product.objects.filter(brand=product.brand, is_published=True)
        .exclude(pk=product.pk)
        .prefetch_related("images")
        .order_by("-updated_at")[:4]
    )
    gallery_images = list(product.images.all())
    primary_image = gallery_images[0] if gallery_images else None
    secondary_images = gallery_images[1:5]
    description_sections = _split_product_description(product.description)
    display_variants = _build_display_variants(product)
    context = {
        "product": product,
        "related_products": related_products,
        "primary_image": primary_image,
        "secondary_images": secondary_images,
        "description_sections": description_sections,
        "display_variants": display_variants,
    }
    return render(request, "meherloom/product_detail.html", context)


def _split_product_description(description):
    if not description:
        return []

    normalized = re.sub(r"\s+", " ", description).strip()
    section_names = ("Shirt", "Dupatta", "Trouser", "Fabric", "Colour")
    tokens = re.split(r"\b(Shirt|Dupatta|Trouser|Fabric|Colour)\b", normalized)

    if len(tokens) <= 1:
        return [{"heading": "Details", "content": normalized}]

    sections = []
    intro = tokens[0].strip()
    if intro:
        sections.append({"heading": "Overview", "content": intro})

    grouped_sections = {}
    index = 1
    while index < len(tokens) - 1:
        heading = tokens[index].strip()
        content = tokens[index + 1].strip(" :")
        if heading in section_names and content:
            grouped_sections.setdefault(heading, [])
            grouped_sections[heading].append(content.strip())
        index += 2

    for heading in section_names:
        if heading in grouped_sections:
            sections.append(
                {
                    "heading": heading,
                    "content": " ".join(grouped_sections[heading]),
                }
            )

    if not sections:
        sections.append({"heading": "Details", "content": normalized})
    return sections


def _build_display_variants(product):
    variants = []
    title = (product.title or "").strip()
    sku = (product.source_sku or "").strip()

    for variant in product.variants.all():
        label = variant.name.strip()
        if title and label.lower().startswith(title.lower()):
            label = label[len(title):].strip(" -|/")
        if sku:
            label = re.sub(rf"^{re.escape(sku)}\s*[-|/]*\s*", "", label, flags=re.IGNORECASE)
        label = re.sub(r"\s*\|\s*", " / ", label).strip(" -|/")
        variants.append(
            {
                "label": label or variant.name,
                "status": variant.get_stock_status_display(),
                "status_value": variant.stock_status,
            }
        )
    return variants


def _parse_decimal(value):
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
