from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.cobweb.publication import available_category_ids, category_available
from apps.configuration.models import LookupEntry


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValidationError(
            "Choisissez un fuseau IANA valide, par exemple Europe/Paris."
        ) from error


def validate_window(instance):
    if instance.starts_at and instance.ends_at and instance.ends_at <= instance.starts_at:
        raise ValidationError({"ends_at": "La fin doit être strictement postérieure au début."})


def in_window(queryset, now=None):
    now = now or timezone.now()
    return queryset.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    )


def publication_queryset(site, *, require_public=True):
    """Une seule politique de lecture publique, sans exception pour les administrateurs."""
    from .models import Product

    entries = list(
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="products",
            lookup_list__field_key="products_product.category",
            lookup_list__kind="category",
        ).select_related("lookup_list")
    )
    available = available_category_ids(entries)
    if (
        site.pk == 1
        or not site.is_active
        or (require_public and not site.public_access_enabled)
        or not site.products_enabled
    ):
        return Product.objects.none(), entries, set()
    items = in_window(
        Product.objects.filter(
            site=site,
            is_active=True,
            is_online=True,
            deleted_at__isnull=True,
            category_id__in=available,
        )
    )
    return (
        items.select_related(
            "site", "category", "picture", "thumbnail", "brand", "language"
        ).order_by("position", "pk"),
        entries,
        available,
    )


def public_variant_queryset(site, *, require_public=True):
    from .models import FeatureDefinition, Variant

    products, _, _ = publication_queryset(site, require_public=require_public)
    items = in_window(
        Variant.objects.filter(
            product__in=products, is_active=True, is_online=True, archived_at__isnull=True
        )
    )
    required = list(
        FeatureDefinition.objects.filter(
            site=site,
            is_active=True,
            required=True,
        ).values_list("pk", flat=True)
    )
    if required:
        items = items.annotate(
            required_values=Count(
                "features",
                filter=Q(features__definition_id__in=required) & ~Q(features__value=""),
                distinct=True,
            )
        ).filter(required_values=len(required))
    return items.select_related("picture").order_by("position", "pk")


def published_variants(product):
    return public_variant_queryset(product.site).filter(product=product)


def public_asset_used(site, asset_id):
    products, _, _ = publication_queryset(site)
    if products.filter(
        Q(picture_id=asset_id)
        | Q(thumbnail_id=asset_id)
        | Q(gallery__asset_id=asset_id, gallery__variant__isnull=True)
    ).exists():
        return True
    variants = public_variant_queryset(site)
    return variants.filter(
        Q(picture_id=asset_id) | Q(gallery__asset_id=asset_id, gallery__product_id=F("product_id"))
    ).exists()


def validate_variant_features(variant):
    from .models import FeatureDefinition, normalized_feature_value

    definitions = FeatureDefinition.objects.filter(
        site_id=variant.product.site_id, is_active=True, required=True
    )
    values = (
        {value.definition_id: value.value for value in variant.features.all()} if variant.pk else {}
    )
    missing = []
    for definition in definitions:
        try:
            if not normalized_feature_value(definition, values.get(definition.pk, "")):
                missing.append(definition.name)
        except ValidationError:
            missing.append(definition.name)
    if missing:
        raise ValidationError(
            {"is_online": "Complétez les caractéristiques requises : " + ", ".join(missing)}
        )


def publication_label(instance):
    if not instance.is_online:
        return "Brouillon"
    if not instance.is_active:
        return "Inactif"
    now = timezone.now()
    if instance.starts_at and instance.starts_at > now:
        return "Publication programmée"
    if instance.ends_at and instance.ends_at <= now:
        return "Publication terminée"
    product = getattr(instance, "product", instance)
    if not product.site.public_access_enabled:
        return "Retiré : exposition publique coupée"
    if not product.category_id or not category_available(product.category):
        return "Retiré : catégorie indisponible"
    if instance is not product and not product.is_online:
        return "Retiré : produit en brouillon"
    if instance is not product:
        if (
            not product.is_active
            or product.deleted_at
            or (product.starts_at and product.starts_at > now)
            or (product.ends_at and product.ends_at <= now)
        ):
            return "Retiré : produit indisponible"
        try:
            validate_variant_features(instance)
        except ValidationError:
            return "Retiré : caractéristiques requises incomplètes"
    return "Publication autorisée"
