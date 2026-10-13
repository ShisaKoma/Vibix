"""Published Cobweb compositions for website consumers."""

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone

from apps.configuration.models import LookupEntry
from apps.core.api import api_get, filter_public_content, paginated_data, public_site
from apps.media.serializers import public_asset_data

from .models import CobwebGroup, CobwebGroupMember, CobwebItem
from .publication import available_category_ids


def published_queryset(site, groups=False):
    model = CobwebGroup if groups else CobwebItem
    if not site.cobweb_enabled:
        return model.objects.none()
    field_key = "cobweb_group.category" if groups else "cobweb_item.category"
    available = available_category_ids(
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="cobweb",
            lookup_list__kind="category",
            lookup_list__field_key__in=["", field_key],
        ).select_related("lookup_list")
    )
    now = timezone.now()
    related = "presentation" if groups else "kind"
    return (
        model.objects.filter(site=site, is_active=True, is_online=True, category_id__in=available)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .select_related("picture", "thumbnail", f"{related}__lookup_list")
        .order_by("position", "pk")
    )


def reference_value(entry, site_id):
    if (
        entry
        and entry.is_active
        and entry.lookup_list.is_active
        and entry.lookup_list.site_id == site_id
    ):
        return entry.value
    return None


def item_data(item):
    link = item.link
    if link:
        try:
            URLValidator(schemes=["http", "https"])(link)
        except ValidationError:
            link = ""
    return {
        "id": item.pk,
        "title": item.title,
        "description": item.subtitle,
        "content_html": str(item.rendered_content),
        "category_id": item.category_id,
        "position": item.position,
        "kind": reference_value(item.kind, item.site_id),
        "link": link,
        "picture": public_asset_data(item.picture, item.site_id),
        "thumbnail": public_asset_data(item.thumbnail, item.site_id),
    }


def group_data(group):
    presentation = reference_value(group.presentation, group.site_id)
    return {
        "id": group.pk,
        "title": group.title,
        "content_html": str(group.rendered_content),
        "category_id": group.category_id,
        "position": group.position,
        "presentation": presentation if presentation in ("cards", "list", "slideshow") else "cards",
        "picture": public_asset_data(group.picture, group.site_id),
        "thumbnail": public_asset_data(group.thumbnail, group.site_id),
        "members_url": reverse("api-public-cobweb-members", args=[group.site_id, group.pk]),
    }


@api_get
def contents(request, site_id, pk=None, groups=False):
    site = public_site(site_id)
    if site is None or not site.cobweb_enabled:
        return JsonResponse({"error": "not_found"}, status=404)
    queryset = published_queryset(site, groups=groups)
    queryset, error = filter_public_content(request, queryset)
    if error is not None:
        return error
    serialize = group_data if groups else item_data
    if pk is not None:
        item = queryset.filter(pk=pk).first()
        if item is None:
            return JsonResponse({"error": "not_found"}, status=404)
        return JsonResponse(serialize(item))
    return paginated_data(request, queryset, serialize)


@api_get
def members(request, site_id, pk):
    site = public_site(site_id)
    if (
        site is None
        or not site.cobweb_enabled
        or not published_queryset(site, groups=True).filter(pk=pk).exists()
    ):
        return JsonResponse({"error": "not_found"}, status=404)
    queryset = (
        CobwebGroupMember.objects.filter(
            group_id=pk, item_id__in=published_queryset(site).values("pk")
        )
        .select_related("item__picture", "item__thumbnail", "item__kind__lookup_list")
        .order_by("position", "pk")
    )
    return paginated_data(
        request,
        queryset,
        lambda member: {"position": member.position, "item": item_data(member.item)},
    )
