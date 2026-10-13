from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError, connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.accounts.models import UserProfile
from apps.addressbook.models import Entry
from apps.articles.models import Article
from apps.cobweb.models import CobwebGroup, CobwebItem
from apps.configuration.views import MANAGE_PERMISSION
from apps.contacts.models import ContactRequest
from apps.core.api import api_get
from apps.media.models import Asset
from apps.pages.models import Page
from apps.planning.models import Booking, Event
from apps.products.models import Product
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission


@require_GET
def health_live(request):
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@login_required
@require_GET
def dashboard(request):
    sites = list(Site.objects.for_user(request.user))
    preferred_site_id = (
        UserProfile.objects.filter(user=request.user)
        .values_list("preferred_site_id", flat=True)
        .first()
    )
    if preferred_site_id:
        sites.sort(key=lambda site: (site.pk != preferred_site_id, site.name))
    site_cards = [
        {
            "site": site,
            "can_manage_settings": has_site_permission(request.user, site, MANAGE_PERMISSION),
        }
        for site in sites
    ]
    return render(request, "core/dashboard.html", {"sites": site_cards})


def site_api_data(request, site):
    return {
        "id": site.pk,
        "code": site.code,
        "name": site.name,
        "description": site.description,
        "icon_url": request.build_absolute_uri(reverse("site-icon", args=[site.pk]))
        if site.icon
        else None,
        "colors": {
            "primary": [site.primary_color_1, site.primary_color_2],
            "secondary": [site.secondary_color_1, site.secondary_color_2],
        },
    }


@login_required
@require_GET
def site_icon(request, site_id):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    if not site.icon:
        raise Http404
    try:
        response = FileResponse(site.icon.open("rb"), content_type="image/png")
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@api_get
def api_sites(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    paginator = Paginator(Site.objects.for_user(request.user), 50)
    page = paginator.get_page(request.GET.get("page", "1"))
    return JsonResponse(
        {
            "count": paginator.count,
            "page": page.number,
            "pages": paginator.num_pages,
            "results": [site_api_data(request, site) for site in page],
        }
    )


@api_get
def api_site_detail(request, pk):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    # Same 404 for missing objects and objects outside the user's scope.
    site = Site.objects.for_user(request.user).filter(pk=pk).first()
    if site is None:
        return JsonResponse({"error": "not_found"}, status=404)
    if not has_site_permission(request.user, site, "tenancy.view_site"):
        return JsonResponse({"error": "permission_denied"}, status=403)
    return JsonResponse(site_api_data(request, site))


@login_required
@require_GET
def workspace(request, site_id):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    module_counts = {}
    for key, model, permission, filters in (
        ("products", Product, "products.view_product", {"deleted_at__isnull": True}),
        ("bookings", Booking, "planning.view_booking", {}),
        ("planning", Event, "planning.view_event", {"deleted_at__isnull": True}),
        ("contacts", ContactRequest, "contacts.view_contactrequest", {"deleted_at__isnull": True}),
        ("addressbook", Entry, "addressbook.view_entry", {"archived_at__isnull": True}),
        ("pages", Page, "pages.view_page", {"deleted_at__isnull": True}),
        ("articles", Article, "articles.view_article", {"deleted_at__isnull": True}),
        ("cobweb", CobwebItem, "cobweb.view_cobwebitem", {}),
        ("groups", CobwebGroup, "cobweb.view_cobwebgroup", {}),
        ("slideshow", CobwebGroup, "cobweb.view_cobwebgroup", {"presentation__value": "slideshow"}),
        ("media", Asset, "media.view_asset", {}),
    ):
        if has_site_permission(request.user, site, permission):
            module_counts[key] = model.objects.filter(site=site, **filters).count()
    return render(
        request,
        "workspace/home.html",
        {"site": site, "module_counts": module_counts},
    )
