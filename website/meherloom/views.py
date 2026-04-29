from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
import re

from .forms import ProductImportForm
from .models import Brand, Product
from .services.catalog import import_product_from_source


PRIMARY_DETAIL_SECTIONS = ("Shirt", "Dupatta", "Trouser", "Culottes")
NARRATIVE_MARKER_PATTERN = r"(Make|Crafted|Elevate|Discover|Step|Designed|This|Our|A|Revamp|Perfect)"
META_MARKER_PATTERN = r"(Model Height:|Model Wears Size:|Model Wears:|View Size Chart)"
PRIMARY_SECTION_PATTERN = re.compile(
    r"\b(Shirt|Dupatta|Trouser|Culottes)\b\s+(.*?)(?=\b(?:Shirt|Dupatta|Trouser|Culottes)\b\s+(?:Printed|Embroidered|Dyed|Digital|Plain|Cotton|Lawn|Blended|Voile|Chiffon|Organza|Khaddar|Colour:|Fabric:)|$)",
    re.IGNORECASE | re.DOTALL,
)


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
    sort_value = request.GET.get("sort", "newest").strip()

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

    sort_map = {
        "newest": "-updated_at",
        "oldest": "updated_at",
        "price_low": "manual_price",
        "price_high": "-manual_price",
        "title_az": "title",
        "title_za": "-title",
    }
    products = products.order_by(sort_map.get(sort_value, "-updated_at"))
    paginator = Paginator(products, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
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
        "products": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "active_brands": active_brands,
        "search_query": query,
        "selected_brand": brand_value,
        "selected_stock": stock_value,
        "min_price": min_price,
        "max_price": max_price,
        "selected_sort": sort_value if sort_value in sort_map else "newest",
        "stock_choices": (
            (Product.StockStatus.IN_STOCK, "In stock"),
            (Product.StockStatus.OUT_OF_STOCK, "Out of stock"),
            (Product.StockStatus.UNKNOWN, "Stock unknown"),
        ),
        "sort_choices": (
            ("newest", "Newest first"),
            ("oldest", "Oldest first"),
            ("price_low", "Price: low to high"),
            ("price_high", "Price: high to low"),
            ("title_az", "Title: A to Z"),
            ("title_za", "Title: Z to A"),
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
    size_guide_html = _render_size_guide_html(product.size_guide)
    size_guide_text = _render_size_guide_text(product.size_guide, size_guide_html)
    size_guide_image_url = product.size_guide_image.url if product.size_guide_image else ""
    context = {
        "product": product,
        "related_products": related_products,
        "primary_image": primary_image,
        "secondary_images": secondary_images,
        "description_sections": description_sections,
        "display_variants": display_variants,
        "size_guide_html": size_guide_html,
        "size_guide_text": size_guide_text,
        "size_guide_image_url": size_guide_image_url,
    }
    return render(request, "meherloom/product_detail.html", context)


def _split_product_description(description):
    if not description:
        return []

    normalized = _normalize_detail_text(description)
    note_text = ""
    note_match = re.search(r"\bNote:\s*(.*)$", normalized, flags=re.IGNORECASE)
    if note_match:
        note_text = note_match.group(1).strip()
        normalized = normalized[:note_match.start()].strip()

    normalized, description_text = _extract_marketing_description(normalized)
    normalized = _infer_missing_first_section(normalized)

    section_matches = list(PRIMARY_SECTION_PATTERN.finditer(normalized))
    if not section_matches:
        sections = [{"heading": "Details", "lines": [normalized]}]
        if description_text:
            sections.append({"heading": "Description", "lines": [description_text]})
        if note_text:
            sections.append({"heading": "Note", "lines": [note_text]})
        return sections

    sections = []
    intro = normalized[: section_matches[0].start()].strip()
    if intro:
        sections.append({"heading": "Overview", "lines": [intro]})

    trailing_description_lines = []

    for match in section_matches:
        heading = match.group(1).strip().title()
        content = match.group(2).strip()
        if heading in PRIMARY_DETAIL_SECTIONS and content:
            lines, overflow_lines = _split_section_lines(content)
            if lines:
                sections.append(
                    {
                        "heading": heading,
                        "lines": lines,
                    }
                )
            if overflow_lines:
                trailing_description_lines.extend(overflow_lines)

    if not sections:
        sections.append({"heading": "Details", "lines": [normalized]})
    description_lines = []
    if trailing_description_lines:
        description_lines.extend(trailing_description_lines)
    if description_text:
        description_lines.append(description_text)
    description_lines, meta_lines = _split_description_and_meta(description_lines)
    if description_lines:
        sections.append({"heading": "Description", "lines": description_lines})
    if meta_lines:
        sections.append({"heading": "Product Notes", "lines": meta_lines})
    if note_text:
        sections.append({"heading": "Note", "lines": [note_text]})
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


def _render_size_guide_html(size_guide):
    if not size_guide:
        return ""
    normalized = size_guide.strip()
    if "<table" in normalized and "overflow-x-auto" in normalized:
        return normalized
    return ""


def _render_size_guide_text(size_guide, size_guide_html=""):
    if not size_guide or size_guide_html:
        return ""
    normalized = re.sub(r"\s+", " ", size_guide).strip()
    if "<div" in normalized or "tab-pane" in normalized or "detail-content-pane" in normalized:
        return ""
    return normalized


def _normalize_detail_text(text):
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"(?<=\D)(\d+-Piece)\b", r" \1", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _split_section_lines(content):
    text = _normalize_detail_text(content)
    text = re.sub(r"\s+(Fabric:)", r"\n\1", text)
    text = re.sub(r"\s+(Colour:)", r"\n\1", text)
    text = re.sub(r"((?:\d+(?:\.\d+)?m)|(?:\d+pc))\s+([A-Z])", r"\1\n\2", text)
    text = "\n".join(re.sub(r"\s+", " ", part).strip() for part in text.split("\n"))
    text = text.replace("\n ", "\n").strip()

    raw_lines = [line.strip(" -|:") for line in text.split("\n") if line.strip(" -|:")]
    lines = []
    overflow_lines = []
    seen = set()
    for line in raw_lines:
        cleaned = re.sub(r"\s+", " ", line).strip()
        detail_line, overflow_line = _split_detail_line_and_overflow(cleaned)
        for split_line in _split_line_sentences(detail_line):
            if split_line and split_line not in seen:
                seen.add(split_line)
                lines.append(split_line)
        if overflow_line:
            overflow_lines.append(overflow_line)
    return lines, overflow_lines


def _split_detail_line_and_overflow(line):
    for prefix in ("Colour:", "Fabric:"):
        if line.startswith(prefix):
            marker_match = re.search(rf"\b{NARRATIVE_MARKER_PATTERN}\b", line)
            if not marker_match:
                marker_match = re.search(META_MARKER_PATTERN, line)
            if marker_match:
                detail_line = line[: marker_match.start()].strip()
                overflow_line = line[marker_match.start() :].strip()
                if detail_line and overflow_line:
                    return detail_line, overflow_line
    return line, ""


def _looks_like_narrative_text(text):
    return bool(re.match(rf"^{NARRATIVE_MARKER_PATTERN}\b", text))


def _extract_marketing_description(text):
    match = re.search(rf"\b{NARRATIVE_MARKER_PATTERN}\b.*$", text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), text[match.start():].strip()


def _infer_missing_first_section(text):
    cleaned = text.strip()
    if cleaned.startswith(":"):
        cleaned = cleaned.lstrip(": ").strip()
        if " Culottes " in f" {cleaned} ":
            return f"Shirt Colour: {cleaned}"
    if cleaned.startswith("Colour:") or cleaned.startswith("Fabric:"):
        if " Culottes " in f" {cleaned} ":
            return f"Shirt {cleaned}"
    return cleaned


def _split_description_and_meta(lines):
    description_lines = []
    meta_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(rf"^{META_MARKER_PATTERN}", line):
            meta_lines.extend(_split_model_meta(line))
            continue
        match = re.search(META_MARKER_PATTERN, line)
        if match:
            description_part = line[: match.start()].strip()
            if description_part:
                description_lines.append(description_part)
            meta_text = line[match.start() :].strip()
            if meta_text:
                meta_lines.extend(_split_model_meta(meta_text))
        else:
            description_lines.append(line)
    return description_lines, meta_lines


def _split_model_meta(text):
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    meta_lines = []
    matches = list(re.finditer(META_MARKER_PATTERN, normalized))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        value = normalized[start:end].strip()
        if value:
            meta_lines.append(value)
    return meta_lines


def _split_line_sentences(line):
    if not line:
        return []
    if ". " not in line:
        return [line]

    segments = [segment.strip() for segment in re.split(r"(?<=\.)\s+", line) if segment.strip()]
    if len(segments) <= 1:
        return [line]
    return segments
