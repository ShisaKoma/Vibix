from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.forms import site_categories
from apps.cobweb.publication import apply_search, available_category_ids, publicly_visible
from apps.cobweb.views import category_tree, readable_links, secure_page
from apps.contentlinks.forms import ContentLinksForm
from apps.contentlinks.models import ContentRecord
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .forms import ArticleForm, ArticleSearchForm, GalleryForm, LifecycleForm
from .models import Article


def context(request, site_id, action="view"):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    if not has_site_permission(request.user, site, "articles.view_article"):
        raise Http404
    if action != "view" and not has_site_permission(
        request.user, site, f"articles.{action}_article"
    ):
        raise Http404
    return {
        "site": site,
        "articles_active": True,
        "can_view_cobweb": has_site_permission(request.user, site, "cobweb.view_cobwebitem"),
        "can_view_groups": has_site_permission(request.user, site, "cobweb.view_cobwebgroup"),
        "can_view_media": has_site_permission(request.user, site, "media.view_asset"),
        "can_manage_settings": has_site_permission(
            request.user, site, "tenancy.manage_site_settings"
        ),
        **{
            f"can_{verb}": has_site_permission(request.user, site, f"articles.{verb}_article")
            for verb in ("add", "change", "delete", "publish", "restore")
        },
    }


def queryset(site):
    return (
        Article.objects.filter(site=site)
        .select_related(
            "site",
            "category__lookup_list",
            "language",
            "picture",
            "thumbnail",
            "created_by",
            "updated_by",
        )
        .prefetch_related("gallery__asset")
    )


def query_string(request, paginate=True):
    keys = [*ArticleSearchForm.base_fields]
    if paginate:
        keys.append("page")
    return urlencode({key: request.GET[key][:200] for key in keys if request.GET.get(key)})


def route(name, site, query="", pk=None):
    url = reverse(f"articles:{name}", args=[site.pk, pk] if pk else [site.pk])
    return f"{url}?{query}" if query else url


def audit(request, article, action):
    AuditEvent.objects.create(
        actor=request.user,
        site=article.site,
        action=f"articles.article.{action}",
        entity_type="articles.article",
        entity_id=str(article.pk),
    )


def search(items, data):
    data = {**data, "order": data["order"] or "-position"}
    items = apply_search(items, data)
    for key, field in (("language", "language"), ("author", "created_by")):
        if data.get(key):
            items = items.filter(**{field: data[key]})
    if data.get("home"):
        items = items.filter(in_home=data["home"] == "yes")
    return items


@secure_page
@login_required
@require_GET
def article_list(request, site_id):
    data = context(request, site_id)
    form = ArticleSearchForm(request.GET, site=data["site"])
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
    return render(request, "articles/list.html", data)


@secure_page
@login_required
@require_GET
def detail(request, site_id, pk):
    data = context(request, site_id)
    article = get_object_or_404(queryset(data["site"]), pk=pk)
    data.update(
        {
            "article": article,
            "links": readable_links(request, article),
            "list_query": query_string(request),
            "return_url": route("list", data["site"], query_string(request)),
        }
    )
    return render(request, "articles/detail.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, site_id, pk=None):
    data = context(request, site_id, "change" if pk else "add")
    site = data["site"]
    status = 200
    with transaction.atomic():
        article = (
            get_object_or_404(
                Article.objects.select_for_update(), site=site, pk=pk, deleted_at__isnull=True
            )
            if pk
            else Article(site=site)
        )
        posted = request.POST if request.method == "POST" else None
        form = ArticleForm(
            posted,
            site=site,
            instance=article,
            can_publish=data["can_publish"],
            can_view_media=data["can_view_media"],
        )
        gallery_form = GalleryForm(
            posted, site=site, article=article, can_view_media=data["can_view_media"]
        )
        links_form = ContentLinksForm(
            posted, site=site, user=request.user, source=article.content_record if pk else None
        )
        if request.method == "POST" and (
            form.is_valid() & gallery_form.is_valid() & links_form.is_valid()
        ):
            if pk and form.cleaned_data["version"] != article.updated_at.isoformat():
                form.add_error(
                    None, "Cet article a changé. Rechargez la fiche avant d’enregistrer."
                )
                status = 409
            else:
                saved = form.save(commit=False)
                if not pk:
                    saved.created_by = request.user
                saved.updated_by = request.user
                if saved.is_online and not saved.published_at:
                    saved.published_at = timezone.now()
                saved.save()
                form.save_m2m()
                gallery_form.save(saved)
                links_form.save(saved.content_record)
                audit(request, saved, "update" if pk else "create")
                messages.success(request, "Article enregistré avec ses médias et liaisons.")
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
            "article": article if pk else None,
            "has_categories": form.fields["category"].queryset.exists(),
            "category_tree": category_tree(form.fields["category"].queryset),
            "additional_tree": category_tree(form.fields["additional_categories"].queryset),
            "selected_category_id": str(form["category"].value() or ""),
            "selected_additional_ids": {
                str(value) for value in form["additional_categories"].value() or []
            },
            "return_url": route("list", site, query_string(request)),
        }
    )
    return render(request, "articles/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, site_id, pk, restore=False):
    data = context(request, site_id, "restore" if restore else "delete")
    status = 200
    with transaction.atomic():
        article = get_object_or_404(
            Article.objects.select_for_update(),
            site=data["site"],
            pk=pk,
            deleted_at__isnull=not restore,
        )
        form = LifecycleForm(
            request.POST if request.method == "POST" else None,
            initial={"version": article.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != article.updated_at.isoformat():
                form.add_error(None, "Cet article a changé. Rechargez la confirmation.")
                status = 409
            else:
                article.deleted_at = None if restore else timezone.now()
                article.is_online = False
                article.updated_by = request.user
                article.save(update_fields=["deleted_at", "is_online", "updated_by", "updated_at"])
                audit(request, article, "restore" if restore else "trash")
                messages.success(
                    request,
                    "Article restauré hors ligne."
                    if restore
                    else "Article déplacé dans la corbeille.",
                )
                return redirect(
                    route("detail" if restore else "list", data["site"], pk=pk if restore else None)
                )
    data.update({"article": article, "form": form, "restore": restore})
    return render(request, "articles/lifecycle.html", data, status=status)


def public_links(article):
    records = ContentRecord.objects.filter(
        incoming_links__source=article.content_record
    ).select_related(
        "item__site",
        "item__category__lookup_list",
        "group__site",
        "group__category__lookup_list",
        "article__site",
        "article__category__lookup_list",
        "page__site",
        "page__category__lookup_list",
        "page__group__lookup_list",
        "page__language",
    )
    return [record for record in records if publicly_visible(record.source)]


@secure_page
@login_required
@require_GET
def preview(request, site_id, pk):
    data = context(request, site_id)
    article = get_object_or_404(queryset(data["site"]), pk=pk)
    return render(
        request,
        "articles/public.html",
        {"content": article, "preview": True, "links": readable_links(request, article)},
    )


@secure_page
@require_GET
def public_detail(request, site_id, pk):
    article = get_object_or_404(queryset(site_id), pk=pk)
    if not publicly_visible(article):
        raise Http404
    return render(
        request, "articles/public.html", {"content": article, "links": public_links(article)}
    )


@secure_page
@require_GET
def public_list(request, site_id):
    site = get_object_or_404(
        Site,
        pk=site_id,
        pk__gt=OWNER_SITE_ID,
        is_active=True,
        public_access_enabled=True,
        articles_enabled=True,
    )
    form = ArticleSearchForm(request.GET, site=site)
    for name in ("author", "trash", "active", "online", "created_from", "created_until"):
        del form.fields[name]
    categories = site_categories(site, "articles_article.category", module="articles")
    category_ids = available_category_ids(categories)
    form.fields["category"].queryset = categories.filter(pk__in=category_ids)
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
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)),
            filters,
        ).defer("content")
    return render(
        request,
        "articles/public_list.html",
        {
            "site": site,
            "search_form": form,
            "page": Paginator(items, 20).get_page(request.GET.get("page")),
            "pagination_query": urlencode(
                {key: request.GET[key][:200] for key in form.fields if request.GET.get(key)}
            ),
        },
    )
