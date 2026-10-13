import re

from django import forms
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from apps.cobweb.content import clean_html
from apps.cobweb.publication import descendant_ids
from apps.core.api import api_get, paginated_data, public_site
from apps.media.serializers import public_asset_data

from .models import Brand, FeatureValue, ProductImage, Tariff
from .pricing import public_tariffs, tariff_data
from .publication import publication_queryset, published_variants


class FiltersForm(forms.Form):
    q = forms.CharField(required=False, max_length=200)
    category_id = forms.IntegerField(required=False, min_value=1, max_value=2**63 - 1)
    brand_id = forms.IntegerField(required=False, min_value=1, max_value=2**63 - 1)
    language = forms.RegexField(r"^[a-zA-Z0-9_-]{1,64}$", required=False)
    featured = forms.ChoiceField(required=False, choices=[("true", "Oui"), ("false", "Non")])
    new = forms.ChoiceField(required=False, choices=[("true", "Oui"), ("false", "Non")])
    in_home = forms.ChoiceField(required=False, choices=[("true", "Oui"), ("false", "Non")])
    reservable = forms.ChoiceField(required=False, choices=[("true", "Oui"), ("false", "Non")])
    order = forms.ChoiceField(
        required=False,
        choices=[
            (value, value)
            for value in (
                "order",
                "title",
                "-title",
                "created_at",
                "-created_at",
            )
        ],
    )

    def clean(self):
        data = super().clean()
        if len(self.data.get("q", "")) > 200:
            self.add_error("q", "La recherche est limitée à 200 caractères.")
        for key in self.fields:
            if key in self.data and key != "q" and self.data.get(key) == "":
                self.add_error(key, "Une valeur explicite est requise.")
            if key in self.data and len(self.data.getlist(key)) != 1:
                self.add_error(key, "Un seul filtre est autorisé pour ce champ.")
            if (
                key in ("category_id", "brand_id")
                and key in self.data
                and not re.fullmatch(r"\d+", self.data.get(key, ""))
            ):
                self.add_error(key, "Indiquez un identifiant entier positif.")
        return data


def filtered_products(request, site):
    items, entries, available = publication_queryset(site)
    form = FiltersForm(request.GET)
    if not form.is_valid():
        return (
            items.none(),
            entries,
            available,
            JsonResponse(
                {"error": "invalid_filters", "fields": list(form.errors)},
                status=400,
            ),
        )
    values = form.cleaned_data
    if values["q"]:
        items = items.filter(
            Q(title__icontains=values["q"])
            | Q(reference__icontains=values["q"])
            | Q(summary__icontains=values["q"])
            | Q(content__icontains=values["q"])
        )
    if values["category_id"]:
        category = next(
            (
                entry
                for entry in entries
                if entry.pk == values["category_id"] and entry.pk in available
            ),
            None,
        )
        if category is None:
            items = items.none()
        else:
            ids = descendant_ids(category) & available
            items = items.filter(
                Q(category_id__in=ids) | Q(additional_categories__pk__in=ids)
            ).distinct()
    if values["brand_id"]:
        items = items.filter(brand_id=values["brand_id"], brand__site=site, brand__is_active=True)
    if values["language"]:
        items = items.filter(url_language=values["language"])
    for key, field in (
        ("featured", "featured"),
        ("new", "is_new"),
        ("in_home", "in_home"),
        ("reservable", "is_reservable"),
    ):
        if values[key]:
            items = items.filter(**{field: values[key] == "true"})
    return (
        items.order_by("position" if values["order"] in ("", "order") else values["order"], "pk"),
        entries,
        available,
        None,
    )


def asset_data(asset, site_id, alt=None):
    if asset is None or asset.site_id != site_id:
        return None
    data = public_asset_data(asset, site_id)
    if alt is not None:
        data["alt_text"] = alt
    return data


def product_data(product):
    brand = (
        product.brand
        if product.brand_id and product.brand.site_id == product.site_id and product.brand.is_active
        else None
    )
    return {
        "id": product.pk,
        "reference": product.reference,
        "title": product.title,
        "subtitle": product.subtitle,
        "summary": product.summary,
        "category_id": product.category_id,
        "category_label": product.category.label,
        "language": product.url_language,
        "brand": {"id": brand.pk, "name": brand.name} if brand else None,
        "position": product.position,
        "featured": product.featured,
        "new": product.is_new,
        "in_home": product.in_home,
        "reservable": product.is_reservable,
        "reservation_status": "internal_only" if product.is_reservable else "not_reservable",
        "picture": asset_data(product.picture, product.site_id),
        "thumbnail": asset_data(product.thumbnail, product.site_id),
    }


def first_page(items, serialize, size=20):
    paginator = Paginator(items, size)
    page = paginator.page(1)
    return {
        "count": paginator.count,
        "page": 1,
        "pages": paginator.num_pages,
        "results": [serialize(item) for item in page],
    }


def images(product, variant=None):
    return product.gallery.filter(variant=variant, asset__site_id=product.site_id).select_related(
        "asset", "product"
    )


def image_data(image):
    return {
        "id": image.pk,
        "position": image.position,
        "asset": asset_data(image.asset, image.product.site_id, image.alt_text),
    }


def features(variant):
    return (
        variant.features.filter(
            definition__site_id=variant.product.site_id,
            definition__is_active=True,
            definition__is_public=True,
        )
        .select_related("definition")
        .order_by("definition__position", "pk")
    )


def feature_data(value):
    return {
        "id": value.definition_id,
        "code": value.definition.code,
        "label": value.definition.name,
        "kind": value.definition.kind,
        "unit": value.definition.unit,
        "value": value.value,
    }


def variant_data(variant):
    product = variant.product
    return {
        "id": variant.pk,
        "reference": variant.reference,
        "title": variant.title,
        "content_html": clean_html(variant.content or product.content),
        "content_inherited": not bool(variant.content),
        "picture_inherited": not bool(variant.picture_id),
        "picture": asset_data(variant.picture or product.picture, product.site_id),
        "position": variant.position,
        "dimensions": {
            key: str(getattr(variant, key)) if getattr(variant, key) is not None else None
            for key in ("length", "width", "height", "weight")
        }
        | {"dimension_unit": variant.dimension_unit, "weight_unit": variant.weight_unit},
        "features": bounded_page(variant.api_features, variant.api_feature_count, feature_data),
        "features_url": reverse(
            "products:api-features", args=[product.site_id, product.pk, variant.pk]
        ),
        "gallery": bounded_page(variant.api_gallery, variant.api_gallery_count, image_data),
        "gallery_url": reverse(
            "products:api-variant-gallery", args=[product.site_id, product.pk, variant.pk]
        ),
        "tariffs": bounded_page(variant.api_tariffs, variant.api_tariff_count, tariff_data),
        "tariffs_url": reverse(
            "products:api-tariffs", args=[product.site_id, product.pk, variant.pk]
        ),
    }


def bounded_page(rows, count, serialize):
    return {
        "count": count,
        "page": 1,
        "pages": max(1, (count + 19) // 20),
        "results": [serialize(row) for row in rows],
    }


def hydrate_variants(items, site):
    """Précharge seulement vingt enfants par variante ; les totaux restent exacts."""
    values = FeatureValue.objects.filter(
        definition__site=site, definition__is_active=True, definition__is_public=True
    )
    gallery = ProductImage.objects.filter(asset__site=site, product_id=OuterRef("product_id"))
    value_count = (
        values.filter(variant_id=OuterRef("pk"))
        .order_by()
        .values("variant_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    gallery_count = (
        gallery.filter(variant_id=OuterRef("pk"))
        .order_by()
        .values("variant_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    tariffs = Tariff.objects.filter(
        variant__product__site=site, is_active=True, is_public=True, archived_at__isnull=True
    ).order_by("position", "pk")
    tariff_count = (
        tariffs.filter(variant_id=OuterRef("pk"))
        .order_by()
        .values("variant_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    image_rows = (
        ProductImage.objects.filter(asset__site=site, product_id=F("variant__product_id"))
        .select_related("asset", "product")
        .order_by("position", "pk")
    )
    return (
        items.select_related("product__picture", "product__site")
        .annotate(
            api_tariff_count=Coalesce(Subquery(tariff_count), Value(0)),
            api_feature_count=Coalesce(Subquery(value_count), Value(0)),
            api_gallery_count=Coalesce(Subquery(gallery_count), Value(0)),
        )
        .prefetch_related(
            Prefetch(
                "features",
                queryset=values.select_related("definition").order_by("definition__position", "pk")[
                    :20
                ],
                to_attr="api_features",
            ),
            Prefetch("gallery", queryset=image_rows[:20], to_attr="api_gallery"),
            Prefetch("tariffs", queryset=tariffs[:20], to_attr="api_tariffs"),
        )
    )


def product_detail(product, available):
    return product_data(product) | {
        "content_html": clean_html(product.content),
        "terms": product.terms,
        "slug": product.slug,
        "seo_title": product.seo_title or product.title,
        "seo_description": product.seo_description or product.summary,
        "additional_category_ids": list(
            product.additional_categories.filter(pk__in=available).values_list("pk", flat=True)[
                :200
            ]
        ),
        "gallery": first_page(images(product), image_data),
        "gallery_url": reverse("products:api-gallery", args=[product.site_id, product.pk]),
        "variants": first_page(
            hydrate_variants(published_variants(product), product.site),
            variant_data,
        ),
        "variants_url": reverse("products:api-variants", args=[product.site_id, product.pk]),
    }


@csrf_exempt
@api_get
def products(request, site_id, pk=None, collection=None, variant_id=None):
    site = public_site(site_id)
    if site is None or not site.products_enabled:
        return JsonResponse({"error": "not_found"}, status=404)
    if error := pagination_error(request):
        return error
    items, _, available = publication_queryset(site)
    if pk is None:
        items, _, _, error = filtered_products(request, site)
        return error if error is not None else paginated_data(request, items, product_data)
    product = items.filter(pk=pk).first()
    if product is None:
        return JsonResponse({"error": "not_found"}, status=404)
    variant = None
    if variant_id is not None:
        variant = (
            hydrate_variants(published_variants(product), site)
            .filter(pk=variant_id)
            .select_related("product__picture", "product__site")
            .first()
        )
        if variant is None:
            return JsonResponse({"error": "not_found"}, status=404)
    if collection == "gallery":
        return paginated_data(request, images(product, variant), image_data)
    if collection == "tariffs" and variant:
        return paginated_data(request, public_tariffs(variant), tariff_data)
    if collection == "features" and variant:
        return paginated_data(request, features(variant), feature_data)
    if collection == "variants":
        return paginated_data(
            request,
            hydrate_variants(published_variants(product), site),
            variant_data,
        )
    return JsonResponse(variant_data(variant) if variant else product_detail(product, available))


def category_counts(items, entries, available):
    """Compte les produits distincts par catégorie et ancêtres, sans donnée privée."""
    nodes = {entry.pk: entry for entry in entries if entry.pk in available}
    counts = {pk: set() for pk in nodes}
    for pk, primary, secondary in (
        items.order_by()
        .values_list("pk", "category_id", "additional_categories__pk")
        .iterator(chunk_size=500)
    ):
        for category in {primary, secondary}:
            seen = set()
            while category in nodes and category not in seen:
                seen.add(category)
                counts[category].add(pk)
                category = nodes[category].parent_id
    return {pk: len(products) for pk, products in counts.items()}


@csrf_exempt
@api_get
def references(request, site_id, brands=False):
    site = public_site(site_id)
    if site is None or not site.products_enabled:
        return JsonResponse({"error": "not_found"}, status=404)
    if error := pagination_error(request):
        return error
    items, entries, available, error = filtered_products(request, site)
    if error is not None:
        return error
    if brands:
        return paginated_data(
            request,
            Brand.objects.filter(site=site, is_active=True, pk__in=items.values("brand_id")),
            lambda brand: {
                "id": brand.pk,
                "code": brand.code,
                "name": brand.name,
                "description": brand.description,
            },
        )
    counts = category_counts(items, entries, available)
    nodes = sorted(
        (entry for entry in entries if counts.get(entry.pk, 0)),
        key=lambda entry: (entry.position, entry.label, entry.pk),
    )
    return paginated_data(
        request,
        nodes,
        lambda entry: {
            "id": entry.pk,
            "parent_id": entry.parent_id,
            "code": entry.value,
            "label": entry.label,
            "position": entry.position,
            "count": counts[entry.pk],
        },
    )


def pagination_error(request):
    for key in ("page", "page_size"):
        if key in request.GET and (
            len(request.GET.getlist(key)) != 1 or not re.fullmatch(r"\d+", request.GET.get(key, ""))
        ):
            return JsonResponse({"error": f"invalid_{key}"}, status=400)
    return None
