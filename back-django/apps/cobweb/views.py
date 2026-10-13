from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry
from apps.configuration.views import MANAGE_PERMISSION
from apps.contentlinks.forms import ContentLinksForm
from apps.contentlinks.models import ContentRecord
from apps.core.navigation import COBWEB_FUNCTIONS, UI_FUNCTIONS
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .availability import active_function_codes
from .forms import (
    CobwebGroupForm,
    CobwebGroupSearchForm,
    CobwebItemForm,
    CobwebSearchForm,
    DeleteConfirmationForm,
    GroupMembersForm,
    site_categories,
)
from .models import CobwebGroup, CobwebGroupMember, CobwebItem
from .publication import apply_search, descendant_ids, publicly_visible


def secure_page(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    return wrapped


def readable_links(request, content):
    records = ContentRecord.objects.filter(
        incoming_links__source=content.content_record
    ).select_related("item", "group", "article", "page", "site")
    return [
        record
        for record in records
        if record.is_available
        and has_site_permission(request.user, content.site, record.permission)
    ]


@secure_page
@require_GET
def public_content(request, site_id, pk, group=False):
    model = CobwebGroup if group else CobwebItem
    content = get_object_or_404(
        model.objects.select_related("site", "category__lookup_list", "picture", "thumbnail"),
        site_id=site_id,
        pk=pk,
    )
    if not publicly_visible(content):
        raise Http404
    members = (
        [
            member.item
            for member in content.members.select_related(
                "item__site", "item__category__lookup_list", "item__thumbnail", "item__picture"
            ).order_by("position", "pk")
            if publicly_visible(member.item)
        ]
        if group
        else []
    )
    links = [
        record
        for record in ContentRecord.objects.filter(
            incoming_links__source=content.content_record
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
        if publicly_visible(record.source)
    ]
    return render(
        request, "cobweb/public.html", {"content": content, "members": members, "links": links}
    )


@secure_page
@login_required
@require_GET
def preview(request, site_id, pk, group=False):
    context = group_context(request, site_id) if group else site_context(request, site_id)
    content = get_object_or_404(CobwebGroup if group else CobwebItem, site=context["site"], pk=pk)
    members = (
        [
            member.item
            for member in content.members.select_related("item").order_by("position", "pk")
        ]
        if group and context["can_view_items"]
        else []
    )
    return render(
        request,
        "cobweb/public.html",
        {
            "content": content,
            "members": members,
            "links": readable_links(request, content),
            "preview": True,
        },
    )


@secure_page
@login_required
@require_GET
def group_detail(request, site_id, pk):
    context = group_context(request, site_id)
    group = get_object_or_404(CobwebGroup, site=context["site"], pk=pk)
    context.update(
        {
            "group": group,
            "links": readable_links(request, group),
            "members": group.members.select_related("item").order_by("position", "pk")
            if context["can_view_items"]
            else [],
        }
    )
    return render(request, "cobweb/group_detail.html", context)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def group_delete(request, site_id, pk):
    context = group_context(request, site_id, "delete")
    status = 200
    with transaction.atomic():
        group = get_object_or_404(
            CobwebGroup.objects.select_for_update(), site=context["site"], pk=pk
        )
        form = DeleteConfirmationForm(
            request.POST if request.method == "POST" else None,
            initial={"version": group.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != group.updated_at.isoformat():
                form.add_error(None, "Le groupe a changé. Rechargez cette confirmation.")
                status = 409
            else:
                AuditEvent.objects.create(
                    actor=request.user,
                    site=context["site"],
                    action="cobweb.group.delete",
                    entity_type="cobweb.cobwebgroup",
                    entity_id=str(group.pk),
                )
                group.delete()
                messages.success(request, "Groupe supprimé ; ses entrées sont conservées.")
                return redirect("cobweb:group-list", site_id=site_id)
    context.update({"group": group, "form": form})
    return render(request, "cobweb/group_delete.html", context, status=status)


def cobweb_function(request):
    match = getattr(request, "resolver_match", None)
    function = (match.kwargs.get("function") if match else None) or request.GET.get("function")
    if function and function not in COBWEB_FUNCTIONS:
        raise Http404
    return function


def function_categories(site, function, *, group=False):
    categories = site_categories(site, "cobweb_group.category" if group else "cobweb_item.category")
    roots = categories.filter(
        value=function, parent__isnull=True, is_active=True, lookup_list__is_active=True
    )
    ids = set()
    for root in roots:
        ids.update(descendant_ids(root))
    return categories.filter(pk__in=ids), roots


def site_context(request, site_id, action="view"):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    if not has_site_permission(request.user, site, "cobweb.view_cobwebitem"):
        raise Http404
    if action != "view" and not has_site_permission(
        request.user, site, f"cobweb.{action}_cobwebitem"
    ):
        raise Http404
    function = cobweb_function(request)
    if function and function not in active_function_codes(site):
        raise Http404
    return {
        "site": site,
        "cobweb_active": True,
        "can_view_cobweb": True,
        "cobweb_function": function,
        "can_manage_settings": has_site_permission(request.user, site, MANAGE_PERMISSION),
        "can_publish": has_site_permission(request.user, site, "cobweb.publish_cobwebitem"),
        "can_view_groups": has_site_permission(request.user, site, "cobweb.view_cobwebgroup"),
        "can_view_media": has_site_permission(request.user, site, "media.view_asset"),
        **{
            f"can_{verb}": has_site_permission(request.user, site, f"cobweb.{verb}_cobwebitem")
            for verb in ("add", "change", "delete")
        },
    }


def query_string(request, *, paginate=True):
    keys = [*CobwebSearchForm.base_fields]
    if paginate:
        keys.append("page")
    params = {key: request.GET[key][:200] for key in keys if request.GET.get(key)}
    function = cobweb_function(request)
    if function:
        params["function"] = function
    return urlencode(params)


def route_url(name, site, query, pk=None):
    url = reverse(f"cobweb:{name}", kwargs={"site_id": site.pk, **({"pk": pk} if pk else {})})
    return f"{url}?{query}" if query else url


def audit(request, site, item, action):
    AuditEvent.objects.create(
        actor=request.user,
        site=site,
        action=f"cobweb.item.{action}",
        entity_type="cobweb.cobwebitem",
        entity_id=str(item.pk),
    )


def category_tree(entries):
    """Produce a deterministic nested structure for the server-rendered tree control."""
    entries = list(entries)
    nodes = {
        entry.pk: {"entry": entry, "id_string": str(entry.pk), "children": []} for entry in entries
    }
    roots = []
    for entry in entries:
        node = nodes[entry.pk]
        if entry.parent_id in nodes:
            nodes[entry.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def group_context(request, site_id, action="view"):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    if not has_site_permission(request.user, site, "cobweb.view_cobwebgroup"):
        raise Http404
    if action != "view" and not has_site_permission(
        request.user, site, f"cobweb.{action}_cobwebgroup"
    ):
        raise Http404
    function = cobweb_function(request)
    if function and function not in active_function_codes(site, group=True):
        raise Http404
    return {
        "site": site,
        "cobweb_active": True,
        "groups_active": True,
        "cobweb_function": function,
        "can_manage_settings": has_site_permission(request.user, site, MANAGE_PERMISSION),
        "can_publish": has_site_permission(request.user, site, "cobweb.publish_cobwebgroup"),
        "can_view_items": has_site_permission(request.user, site, "cobweb.view_cobwebitem"),
        "can_view_function_items": not function or function in active_function_codes(site),
        "can_view_cobweb": has_site_permission(request.user, site, "cobweb.view_cobwebitem"),
        "can_view_groups": True,
        "can_view_media": has_site_permission(request.user, site, "media.view_asset"),
        **{
            f"can_group_{verb}": has_site_permission(
                request.user, site, f"cobweb.{verb}_cobwebgroup"
            )
            for verb in ("add", "change", "delete")
        },
    }


@secure_page
@login_required
@require_GET
def item_list(request, site_id, function=None):
    context = site_context(request, site_id)
    form = CobwebSearchForm(request.GET, site=context["site"])
    items = CobwebItem.objects.filter(site=context["site"]).select_related("category__lookup_list")
    function = context["cobweb_function"]
    if function:
        categories, roots = function_categories(context["site"], function)
        form.fields["category"].queryset = categories
        items = items.filter(
            Q(category__in=categories) | Q(additional_categories__in=categories)
        ).distinct()
        context["function_ui"] = COBWEB_FUNCTIONS[function]
        context["function_label"] = UI_FUNCTIONS[COBWEB_FUNCTIONS[function]]["label"]
    if form.is_valid():
        items = apply_search(items, form.cleaned_data)
    else:
        items = items.none()
    context.update(
        {
            "search_form": form,
            "page": Paginator(items, 20).get_page(request.GET.get("page")),
            "list_query": query_string(request),
            "pagination_query": query_string(request, paginate=False),
            "advanced_open": any(
                request.GET.get(key)
                for key in (
                    "category",
                    "active",
                    "online",
                    "created_from",
                    "created_until",
                    "order",
                )
            )
            or bool(form.errors),
        }
    )
    return render(request, "cobweb/list.html", context)


@secure_page
@login_required
@require_GET
def item_detail(request, site_id, pk):
    context = site_context(request, site_id)
    item = get_object_or_404(
        CobwebItem.objects.select_related("category__lookup_list"), site=context["site"], pk=pk
    )
    query = query_string(request)
    context.update(
        {"item": item, "list_query": query, "return_url": route_url("list", context["site"], query)}
    )
    context["links"] = readable_links(request, item)
    context["memberships"] = (
        item.group_memberships.select_related("group") if context["can_view_groups"] else []
    )
    return render(request, "cobweb/detail.html", context)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def item_edit(request, site_id, pk=None):
    context = site_context(request, site_id, "change" if pk else "add")
    site = context["site"]
    query = query_string(request)
    response_status = 200
    with transaction.atomic():
        item = (
            get_object_or_404(CobwebItem.objects.select_for_update(), site=site, pk=pk)
            if pk
            else CobwebItem(site=site)
        )
        form = CobwebItemForm(
            request.POST if request.method == "POST" else None,
            instance=item,
            site=site,
            can_publish=context["can_publish"],
            can_view_media=context["can_view_media"],
        )
        if not pk and request.method == "GET" and context["cobweb_function"]:
            _, roots = function_categories(site, context["cobweb_function"])
            category = form.fields["category"].queryset.filter(pk__in=roots).first()
            if category:
                form.initial["category"] = category.pk
        links_form = ContentLinksForm(
            request.POST if request.method == "POST" else None,
            site=site,
            user=request.user,
            source=item.content_record if item.pk else None,
        )
        forms_valid = form.is_valid() & links_form.is_valid() if request.method == "POST" else False
        if forms_valid:
            if pk and form.cleaned_data["version"] != item.updated_at.isoformat():
                form.add_error(
                    None,
                    "Cette entrée a été modifiée depuis son ouverture. "
                    "Rechargez la fiche avant de reprendre vos changements.",
                )
                response_status = 409
            else:
                saved = form.save()
                links_form.save(saved.content_record)
                audit(request, site, saved, "update" if pk else "create")
                messages.success(request, "Entrée Cobweb enregistrée.")
                if request.POST.get("save_action") == "continue":
                    return redirect(route_url("edit", site, query, saved.pk))
                return redirect(route_url("detail", site, query, saved.pk))
    context.update(
        {
            "form": form,
            "item": item if pk else None,
            "list_query": query,
            "return_url": route_url("list", site, query),
            "has_categories": form.fields["category"].queryset.exists(),
            "category_tree": category_tree(form.fields["category"].queryset),
            "selected_category_id": str(form["category"].value() or ""),
            "selected_additional_ids": {
                str(pk) for pk in (form["additional_categories"].value() or [])
            },
            "additional_tree": category_tree(form.fields["additional_categories"].queryset),
            "links_form": links_form,
            "memberships": item.group_memberships.select_related("group")
            if pk and context["can_view_groups"]
            else [],
        }
    )
    return render(request, "cobweb/form.html", context, status=response_status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def item_delete(request, site_id, pk):
    context = site_context(request, site_id, "delete")
    site = context["site"]
    query = query_string(request)
    response_status = 200
    with transaction.atomic():
        item = get_object_or_404(CobwebItem.objects.select_for_update(), site=site, pk=pk)
        form = DeleteConfirmationForm(
            request.POST if request.method == "POST" else None,
            initial={"version": item.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != item.updated_at.isoformat():
                form.add_error(
                    None,
                    "Cette entrée a changé. Rechargez la confirmation pour vérifier son contenu.",
                )
                response_status = 409
            else:
                try:
                    with transaction.atomic():
                        audit(request, site, item, "delete")
                        item.delete()
                except ProtectedError:
                    form.add_error(
                        None,
                        "Cette entrée appartient à un groupe. "
                        "Retirez ses liaisons de composition avant de la supprimer.",
                    )
                    response_status = 409
                else:
                    messages.success(request, "Entrée Cobweb supprimée.")
                    return redirect(route_url("list", site, query))
    context.update({"item": item, "form": form, "return_url": route_url("detail", site, query, pk)})
    return render(request, "cobweb/delete.html", context, status=response_status)


@secure_page
@login_required
@require_GET
def group_list(request, site_id, slideshow=False):
    context = group_context(request, site_id)
    groups = CobwebGroup.objects.filter(site=context["site"]).select_related(
        "category__lookup_list"
    )
    groups = groups.prefetch_related("members")
    slideshow_mode = slideshow or request.GET.get("presentation") == "slideshow"
    if slideshow_mode:
        groups = groups.filter(presentation__value="slideshow")
    form = CobwebGroupSearchForm(request.GET, site=context["site"])
    function = context["cobweb_function"]
    if function:
        categories, _ = function_categories(context["site"], function, group=True)
        form.fields["category"].queryset = categories
        groups = groups.filter(
            Q(category__in=categories) | Q(additional_categories__in=categories)
        ).distinct()
        context["function_ui"] = COBWEB_FUNCTIONS[function]
        context["function_label"] = UI_FUNCTIONS[COBWEB_FUNCTIONS[function]]["label"]
    groups = apply_search(groups, form.cleaned_data) if form.is_valid() else groups.none()
    context.update(
        {
            "groups": groups,
            "search_form": form,
            "page": Paginator(groups, 20).get_page(request.GET.get("page")),
            "list_query": query_string(request),
            "pagination_query": query_string(request, paginate=False),
            "slideshow_mode": slideshow_mode,
        }
    )
    return render(request, "cobweb/group_list.html", context)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def group_edit(request, site_id, pk=None):
    context = group_context(request, site_id, "change" if pk else "add")
    site = context["site"]
    response_status = 200
    if not context["can_view_items"]:
        raise Http404
    with transaction.atomic():
        group = (
            get_object_or_404(CobwebGroup.objects.select_for_update(), site=site, pk=pk)
            if pk
            else CobwebGroup(site=site)
        )
        if not pk and request.GET.get("presentation") == "slideshow":
            initial_presentation = (
                LookupEntry.objects.filter(
                    lookup_list__site=site,
                    lookup_list__module="cobweb",
                    lookup_list__kind="choice",
                    lookup_list__field_key="cobweb_group.presentation",
                    lookup_list__is_active=True,
                    value="slideshow",
                    is_active=True,
                )
                .values_list("pk", flat=True)
                .first()
            )
        else:
            initial_presentation = None
        existing = dict(group.members.values_list("item_id", "position")) if group.pk else {}
        group_form = CobwebGroupForm(
            request.POST if request.method == "POST" else None,
            instance=group,
            initial={"presentation": initial_presentation} if initial_presentation else None,
            site=site,
            can_publish=context["can_publish"],
            can_view_media=context["can_view_media"],
        )
        if not pk and request.method == "GET" and request.GET.get("presentation") == "slideshow":
            category = group_form.fields["category"].queryset.filter(value="slideshow").first()
            if category:
                group_form.initial["category"] = category.pk
        if not pk and request.method == "GET" and context["cobweb_function"]:
            _, roots = function_categories(site, context["cobweb_function"], group=True)
            category = group_form.fields["category"].queryset.filter(pk__in=roots).first()
            if category:
                group_form.initial["category"] = category.pk
        members_form = GroupMembersForm(
            request.POST if request.method == "POST" else None, site=site, existing=existing
        )
        slideshow_mode = request.GET.get("presentation") == "slideshow" or (
            group.presentation_id and group.presentation.value == "slideshow"
        )
        if slideshow_mode:
            members_form.items = members_form.items.filter(
                Q(picture__isnull=False) | Q(thumbnail__isnull=False)
            )
            members_form.fields["members"].choices = [
                (str(item.pk), item.title) for item in members_form.items
            ]
        links_form = ContentLinksForm(
            request.POST if request.method == "POST" else None,
            site=site,
            user=request.user,
            source=group.content_record if group.pk else None,
        )
        forms_valid = (
            group_form.is_valid() & members_form.is_valid() & links_form.is_valid()
            if request.method == "POST"
            else False
        )
        if (
            forms_valid
            and group_form.cleaned_data["presentation"]
            and group_form.cleaned_data["presentation"].value == "slideshow"
        ):
            selected_image_ids = set(
                CobwebItem.objects.filter(
                    site=site,
                    pk__in=members_form.cleaned_data["member_ids"],
                )
                .filter(Q(picture__isnull=False) | Q(thumbnail__isnull=False))
                .values_list("pk", flat=True)
            )
            if selected_image_ids != members_form.cleaned_data["member_ids"]:
                members_form.add_error(
                    "members", "Un diaporama ne peut contenir que des entrées avec une image."
                )
                forms_valid = False
        if forms_valid:
            if pk and group_form.cleaned_data["version"] != group.updated_at.isoformat():
                group_form.add_error(
                    None,
                    "Ce groupe a été modifié depuis son ouverture. "
                    "Rechargez-le avant de reprendre vos changements.",
                )
                response_status = 409
            else:
                saved = group_form.save()
                links_form.save(saved.content_record)
                selected = members_form.cleaned_data["member_positions"]
                CobwebGroupMember.objects.filter(group=saved).delete()
                CobwebGroupMember.objects.bulk_create(
                    [
                        CobwebGroupMember(group=saved, item_id=item_id, position=position)
                        for item_id, position in selected.items()
                    ]
                )
                AuditEvent.objects.create(
                    actor=request.user,
                    site=site,
                    action=f"cobweb.group.{'update' if pk else 'create'}",
                    entity_type="cobweb.cobwebgroup",
                    entity_id=str(saved.pk),
                )
                messages.success(request, "Groupe Cobweb enregistré avec ses liaisons.")
                action = (
                    "group-edit"
                    if request.POST.get("save_action") == "continue"
                    else "group-detail"
                )
                return redirect(route_url(action, site, query_string(request), saved.pk))
    selected_ids = (
        set(request.POST.getlist("members"))
        if request.method == "POST"
        else {str(pk) for pk in existing}
    )
    context.update(
        {
            "group": group if pk else None,
            "group_form": group_form,
            "members_form": members_form,
            "member_rows": [
                {
                    "item": item,
                    "selected": str(item.pk) in selected_ids,
                    "position": request.POST.get(f"member_position_{item.pk}", "0")
                    if request.method == "POST"
                    else existing.get(item.pk, 0),
                }
                for item in members_form.items
            ],
            "category_tree": category_tree(group_form.fields["category"].queryset),
            "selected_category_id": str(group_form["category"].value() or ""),
            "has_categories": group_form.fields["category"].queryset.exists(),
            "selected_additional_ids": {
                str(pk) for pk in (group_form["additional_categories"].value() or [])
            },
            "additional_tree": category_tree(group_form.fields["additional_categories"].queryset),
            "links_form": links_form,
            "list_query": query_string(request),
        }
    )
    return render(request, "cobweb/group_form.html", context, status=response_status)
