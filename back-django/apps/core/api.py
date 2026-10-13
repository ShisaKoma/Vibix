"""Read-only public contract for the website, separate from private site APIs."""

import re
from functools import wraps

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q
from django.http import FileResponse, JsonResponse
from django.utils import timezone

from apps.articles.models import Article
from apps.cobweb.publication import available_category_ids
from apps.configuration.models import LookupEntry
from apps.media.serializers import public_asset_data
from apps.pages.models import Page
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site


def api_get(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method != "GET":
            response = JsonResponse({"error": "method_not_allowed"}, status=405)
            response["Allow"] = "GET"
        else:
            response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    return wrapped


@api_get
def openapi(request):
    return FileResponse(
        (settings.BASE_DIR / "docs" / "openapi.yaml").open("rb"),
        content_type="application/yaml; charset=utf-8",
    )


def public_site(site_id):
    return (
        Site.objects.filter(pk=site_id, is_active=True, public_access_enabled=True)
        .exclude(pk=OWNER_SITE_ID)
        .first()
    )


@api_get
def site_settings(request, site_id):
    site = public_site(site_id)
    if site is None:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse(
        {
            "contacts_enabled": site.contacts_enabled,
            "planning_enabled": site.planning_enabled,
            "products_enabled": site.products_enabled,
            "articles_enabled": site.articles_enabled,
            "pages_enabled": site.pages_enabled,
            "cobweb_enabled": site.cobweb_enabled,
            "id": site.pk,
            "name": site.name,
            "description": site.description,
            "frontend_options": frontend_options(site),
            "colors": {
                "primary": [site.primary_color_1, site.primary_color_2],
                "secondary": [site.secondary_color_1, site.secondary_color_2],
            },
        }
    )


def frontend_options(site):
    """Only the two public client references, never arbitrary configuration lists."""
    entries = (
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="api",
            lookup_list__kind="choice",
            lookup_list__is_active=True,
            is_active=True,
            lookup_list__field_key__in=("api_client.template", "api_client.color_scheme"),
        )
        .select_related("lookup_list")
        .order_by("position", "pk")
    )
    result = {"templates": [], "color_schemes": []}
    seen = set()
    for entry in entries:
        field = entry.lookup_list.field_key
        if (field, entry.value) in seen:
            continue
        seen.add((field, entry.value))
        key = "templates" if field == "api_client.template" else "color_schemes"
        result[key].append({"value": entry.value, "label": entry.label})
    return result


def publication_queryset(site, module):
    model = {"articles": Article, "pages": Page}[module]
    if not site.module_enabled(module):
        return model.objects.none(), [], set()
    entries = list(
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module=module,
            lookup_list__field_key__in=[
                f"{module}_{model._meta.model_name}.category",
                "pages_page.group",
            ],
        ).select_related("lookup_list")
    )
    available = available_category_ids(entries)
    now = timezone.now()
    queryset = (
        model.objects.filter(
            site=site,
            deleted_at__isnull=True,
            is_active=True,
            is_online=True,
            category_id__in=available,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    )
    if module == "pages":
        queryset = queryset.filter(Q(group__isnull=True) | Q(group_id__in=available))
    return (
        queryset.select_related("picture", "thumbnail")
        .prefetch_related("gallery__asset")
        .order_by("-position", "pk"),
        entries,
        available,
    )


def content_data(item):
    data = {
        "id": item.pk,
        "title": item.title,
        "description": item.subtitle,
        "content_html": str(item.rendered_content),
        "category_id": item.category_id,
        "position": item.position,
        "picture": public_asset_data(item.picture, item.site_id),
        "thumbnail": public_asset_data(item.thumbnail, item.site_id),
        "gallery": [
            {"position": image.position, "asset": public_asset_data(image.asset, item.site_id)}
            for image in item.gallery.all()
            if image.asset.site_id == item.site_id
        ],
    }
    if isinstance(item, Page):
        data.update(group_id=item.group_id, slug=item.slug, language=item.url_language)
    else:
        data["in_home"] = item.in_home
    return data


def filter_public_content(request, queryset, *, allow_home=False):
    """Apply bounded, site-scoped public filters; never accept arbitrary ORM fields."""
    category_id = request.GET.get("category_id")
    if category_id is not None:
        try:
            value = int(category_id)
            if value < 1 or value > 2**63 - 1:
                raise ValueError
        except (TypeError, ValueError):
            return None, JsonResponse({"error": "invalid_category_id"}, status=400)
        queryset = queryset.filter(category_id=value)
    category_code = request.GET.get("category_code")
    if category_code is not None:
        if not re.fullmatch(r"[-a-zA-Z0-9_]{1,64}", category_code):
            return None, JsonResponse({"error": "invalid_category_code"}, status=400)
        queryset = queryset.filter(category__value=category_code)
    in_home = request.GET.get("in_home")
    if in_home is not None:
        if not allow_home or in_home not in ("true", "false"):
            return None, JsonResponse({"error": "invalid_in_home"}, status=400)
        queryset = queryset.filter(in_home=in_home == "true")
    return queryset, None


@api_get
def contents(request, site_id, module, pk=None):
    site = public_site(site_id)
    if site is None or module not in ("pages", "articles") or not site.module_enabled(module):
        return JsonResponse({"error": "not_found"}, status=404)
    queryset, entries, available = publication_queryset(site, module)
    queryset, error = filter_public_content(request, queryset, allow_home=module == "articles")
    if error is not None:
        return error
    if pk is not None:
        item = queryset.filter(pk=pk).first()
        if item is None:
            return JsonResponse({"error": "not_found"}, status=404)
        return JsonResponse(content_data(item))
    return paginated_data(
        request,
        queryset,
        content_data,
        navigation=[
            {
                "id": entry.pk,
                "parent_id": entry.parent_id,
                "label": entry.label,
                "code": entry.value,
                "position": entry.position,
                "field": entry.lookup_list.field_key,
            }
            for entry in entries
            if entry.pk in available
        ],
    )


def paginated_data(request, queryset, serialize, **extra):
    """Use the same bounded pagination and errors for each public collection."""
    try:
        number = int(request.GET.get("page", "1"))
        if number < 1:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({"error": "invalid_page"}, status=400)
    try:
        page_size = int(request.GET.get("page_size", "20"))
        if page_size < 1 or page_size > 100:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({"error": "invalid_page_size"}, status=400)
    paginator = Paginator(queryset, page_size)
    try:
        page = paginator.page(number)
    except EmptyPage:
        return JsonResponse({"error": "invalid_page"}, status=400)
    return JsonResponse(
        {
            "count": paginator.count,
            "page": page.number,
            "pages": paginator.num_pages,
            "results": [serialize(item) for item in page],
            **extra,
        }
    )
