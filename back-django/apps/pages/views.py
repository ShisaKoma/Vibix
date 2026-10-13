from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.forms import site_categories
from apps.cobweb.publication import (
    apply_search,
    available_category_ids,
    descendant_ids,
    publicly_visible,
)
from apps.cobweb.views import category_tree, readable_links, secure_page
from apps.contentlinks.forms import ContentLinksForm
from apps.contentlinks.models import ContentRecord
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .forms import GalleryForm, LifecycleForm, PageForm, PageSearchForm
from .models import Page


def context(request, site_id, action="view"):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    if not has_site_permission(request.user, site, "pages.view_page"):
        raise Http404
    if action != "view" and not has_site_permission(request.user, site, f"pages.{action}_page"):
        raise Http404
    return {
        "site": site,
        "pages_active": True,
        "can_view_cobweb": has_site_permission(request.user, site, "cobweb.view_cobwebitem"),
        "can_view_groups": has_site_permission(request.user, site, "cobweb.view_cobwebgroup"),
        "can_view_media": has_site_permission(request.user, site, "media.view_asset"),
        "can_manage_settings": has_site_permission(
            request.user, site, "tenancy.manage_site_settings"
        ),
        **{
            f"can_{verb}": has_site_permission(request.user, site, f"pages.{verb}_page")
            for verb in ("add", "change", "delete", "publish", "restore")
        },
    }


def queryset(site):
    return (
        Page.objects.filter(site=site)
        .select_related(
            "site",
            "category__lookup_list",
            "language",
            "group__lookup_list",
            "content_record",
            "picture",
            "thumbnail",
            "created_by",
            "updated_by",
        )
        .prefetch_related("gallery__asset", "additional_categories")
    )


def query_string(request, paginate=True):
    keys = [*PageSearchForm.base_fields]
    if paginate:
        keys.append("page")
    return urlencode({key: request.GET[key][:200] for key in keys if request.GET.get(key)})


def route(name, site, query="", pk=None):
    url = reverse(f"pages:{name}", args=[site.pk, pk] if pk else [site.pk])
    return f"{url}?{query}" if query else url


def audit(request, page, action):
    AuditEvent.objects.create(
        actor=request.user,
        site=page.site,
        action=f"pages.page.{action}",
        entity_type="pages.page",
        entity_id=str(page.pk),
    )


def search(items, data):
    data = {**data, "order": data["order"] or "-position"}
    items = apply_search(items, data)
    for key, field in (("language", "language"), ("author", "created_by")):
        if data.get(key):
            items = items.filter(**{field: data[key]})
    if data.get("group"):
        ids = descendant_ids(data["group"]) if data["include_descendants"] else {data["group"].pk}
        items = items.filter(group_id__in=ids)
    return items


@secure_page
@login_required
@require_GET
def page_list(request, site_id):
    data = context(request, site_id)
    form = PageSearchForm(request.GET, site=data["site"])
    items = queryset(data["site"])
    if form.is_valid():
        items = search(
            items.filter(deleted_at__isnull=form.cleaned_data["trash"] != "yes"), form.cleaned_data
        )
    else:
        items = items.none()
    data.update(
        {
            "search_form": form,
            "page": Paginator(items, 20).get_page(request.GET.get("page")),
            "list_query": query_string(request),
            "pagination_query": query_string(request, False),
            "advanced_open": bool(form.errors)
            or any(request.GET.get(key) for key in form.fields if key != "q"),
        }
    )
    return render(request, "pages/list.html", data)


@secure_page
@login_required
@require_GET
def detail(request, site_id, pk):
    data = context(request, site_id)
    page = get_object_or_404(queryset(data["site"]), pk=pk)
    data.update(
        {
            "entry": page,
            "links": readable_links(request, page),
            "list_query": query_string(request),
            "return_url": route("list", data["site"], query_string(request)),
        }
    )
    return render(request, "pages/detail.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, site_id, pk=None):
    data = context(request, site_id, "change" if pk else "add")
    site = data["site"]
    status = 200
    with transaction.atomic():
        page = (
            get_object_or_404(
                Page.objects.select_for_update(), site=site, pk=pk, deleted_at__isnull=True
            )
            if pk
            else Page(site=site)
        )
        posted = request.POST if request.method == "POST" else None
        form = PageForm(
            posted,
            site=site,
            instance=page,
            can_publish=data["can_publish"],
            can_view_media=data["can_view_media"],
        )
        gallery_form = GalleryForm(
            posted, site=site, page=page, can_view_media=data["can_view_media"]
        )
        links_form = ContentLinksForm(
            posted, site=site, user=request.user, source=page.content_record if pk else None
        )
        if request.method == "POST" and (
            form.is_valid() & gallery_form.is_valid() & links_form.is_valid()
        ):
            if pk and form.cleaned_data["version"] != page.updated_at.isoformat():
                form.add_error(None, "Cette page a changé. Rechargez la fiche avant d’enregistrer.")
                status = 409
            else:
                saved = form.save(commit=False)
                if not pk:
                    saved.created_by = request.user
                saved.updated_by = request.user
                if saved.is_online and not saved.published_at:
                    saved.published_at = timezone.now()
                try:
                    with transaction.atomic():
                        saved.save()
                except IntegrityError as error:
                    if (
                        getattr(error.__cause__, "diag", None)
                        and error.__cause__.diag.constraint_name == "unique_page_url"
                    ):
                        form.add_error(
                            "slug",
                            "Cette adresse vient d’être utilisée. Choisissez une autre adresse.",
                        )
                        status = 409
                    else:
                        raise
                if not form.errors:
                    form.save_m2m()
                    gallery_form.save(saved)
                    links_form.save(saved.content_record)
                    audit(request, saved, "update" if pk else "create")
                    messages.success(request, "Page enregistrée avec ses médias et liaisons.")
                    return redirect(
                        route(
                            "edit" if request.POST.get("save_action") == "continue" else "detail",
                            site,
                            query_string(request),
                            saved.pk,
                        )
                    )
    data.update(
        {
            "form": form,
            "gallery_form": gallery_form,
            "links_form": links_form,
            "entry": page if pk else None,
            "has_categories": form.fields["category"].queryset.exists(),
            "category_tree": category_tree(form.fields["category"].queryset),
            "group_tree": category_tree(form.fields["group"].queryset),
            "selected_group_id": str(form["group"].value() or ""),
            "additional_tree": category_tree(form.fields["additional_categories"].queryset),
            "selected_category_id": str(form["category"].value() or ""),
            "selected_additional_ids": {
                str(value) for value in form["additional_categories"].value() or []
            },
            "return_url": route("list", site, query_string(request)),
        }
    )
    return render(request, "pages/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, site_id, pk, restore=False):
    data = context(request, site_id, "restore" if restore else "delete")
    status = 200
    with transaction.atomic():
        page = get_object_or_404(
            Page.objects.select_for_update(),
            site=data["site"],
            pk=pk,
            deleted_at__isnull=not restore,
        )
        form = LifecycleForm(
            request.POST if request.method == "POST" else None,
            initial={"version": page.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != page.updated_at.isoformat():
                form.add_error(None, "Cette page a changé. Rechargez la confirmation.")
                status = 409
            else:
                page.deleted_at = None if restore else timezone.now()
                page.is_online = False
                page.updated_by = request.user
                page.save(update_fields=["deleted_at", "is_online", "updated_by", "updated_at"])
                audit(request, page, "restore" if restore else "trash")
                messages.success(
                    request,
                    "Page restaurée hors ligne." if restore else "Page déplacée dans la corbeille.",
                )
                return redirect(
                    route("detail" if restore else "list", data["site"], pk=pk if restore else None)
                )
    data.update({"entry": page, "form": form, "restore": restore})
    return render(request, "pages/lifecycle.html", data, status=status)


def public_links(page):
    records = ContentRecord.objects.filter(
        incoming_links__source=page.content_record
    ).select_related(
        "item__site",
        "item__category__lookup_list",
        "group__site",
        "group__category__lookup_list",
        "page__site",
        "page__category__lookup_list",
        "page__group__lookup_list",
        "page__language",
        "article__site",
        "article__category__lookup_list",
    )
    return [record for record in records if publicly_visible(record.source)]


@secure_page
@login_required
@require_GET
def preview(request, site_id, pk):
    data = context(request, site_id)
    page = get_object_or_404(queryset(data["site"]), pk=pk)
    return render(
        request,
        "pages/public.html",
        {"content": page, "preview": True, "links": readable_links(request, page)},
    )


@secure_page
@require_GET
def public_detail(request, site_id, pk=None, language=None, slug=None):
    filters = {"pk": pk} if pk is not None else {"slug": slug, "url_language": language}
    page = get_object_or_404(queryset(site_id), **filters)
    if not publicly_visible(page):
        raise Http404
    return render(request, "pages/public.html", {"content": page, "links": public_links(page)})


@secure_page
@require_GET
def public_list(request, site_id):
    site = get_object_or_404(
        Site,
        pk=site_id,
        pk__gt=OWNER_SITE_ID,
        is_active=True,
        public_access_enabled=True,
        pages_enabled=True,
    )
    form = PageSearchForm(request.GET, site=site)
    for name in ("author", "trash", "active", "online", "created_from", "created_until"):
        del form.fields[name]
    categories = site_categories(site, "pages_page.category", module="pages")
    category_ids = available_category_ids(categories)
    form.fields["category"].queryset = categories.filter(pk__in=category_ids)
    groups = form.fields["group"].queryset
    group_ids = available_category_ids(groups)
    form.fields["group"].queryset = groups.filter(pk__in=group_ids)
    form.fields["language"].queryset = form.fields["language"].queryset.filter(
        is_active=True, lookup_list__is_active=True
    )
    items = queryset(site).none()
    if form.is_valid():
        filters = {
            "active": "yes",
            "online": "yes",
            "created_from": None,
            "created_until": None,
            **form.cleaned_data,
        }
        now = timezone.now()
        items = search(
            queryset(site)
            .filter(
                deleted_at__isnull=True,
                category_id__in=category_ids,
            )
            .filter(Q(group__isnull=True) | Q(group_id__in=group_ids))
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)),
            filters,
        ).defer("content")
    return render(
        request,
        "pages/public_list.html",
        {
            "site": site,
            "search_form": form,
            "page": Paginator(items, 20).get_page(request.GET.get("page")),
            "pagination_query": urlencode(
                {key: request.GET[key][:200] for key in form.fields if request.GET.get(key)}
            ),
        },
    )
