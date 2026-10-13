from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.views import secure_page
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .forms import CHILD_FORMS, EntryForm, EntryTypeForm, SearchForm, VersionForm
from .models import Employment, Entry, EntryType


def context(request, site_id, action="view"):
    site = get_object_or_404(
        Site.objects.for_user(request.user).exclude(pk=OWNER_SITE_ID), pk=site_id
    )
    for verb in {"view", action}:
        if not has_site_permission(request.user, site, f"addressbook.{verb}_entry"):
            raise Http404
    return {
        "site": site,
        "addressbook_active": True,
        "can_manage_types": has_site_permission(request.user, site, "tenancy.manage_site_settings"),
        **{
            f"can_{verb}": has_site_permission(request.user, site, f"addressbook.{verb}_entry")
            for verb in ("add", "change", "delete", "restore")
        },
    }


def entries(site):
    return Entry.objects.filter(site=site).select_related("category", "legal_form", "origin")


def audit(request, entry, action, child=None):
    AuditEvent.objects.create(
        actor=request.user,
        site=entry.site,
        action=f"addressbook.{action}",
        entity_type=f"addressbook.{child._meta.model_name}" if child else "addressbook.entry",
        entity_id=str(child.pk if child else entry.pk),
    )


def current_version(form, entry):
    if form.cleaned_data["version"] != entry.updated_at.isoformat():
        form.add_error(None, "Cette fiche a changé. Rechargez-la avant d’enregistrer.")
        return False
    return True


@secure_page
@login_required
@require_GET
def entry_list(request, site_id):
    data = context(request, site_id)
    form = SearchForm(request.GET, site=data["site"])
    items = entries(data["site"])
    if form.is_valid():
        values = form.cleaned_data
        state = values["state"] or "active"
        if state == "archived":
            items = items.filter(archived_at__isnull=False)
        elif state != "all":
            items = items.filter(archived_at__isnull=True, is_active=state == "active")
        for field in ("kind", "category"):
            if values[field]:
                items = items.filter(**{field: values[field]})
        if values["q"]:
            q = values["q"]
            items = items.filter(
                Q(name__icontains=q)
                | Q(first_name__icontains=q)
                | Q(reference__icontains=q)
                | Q(contacts__value__icontains=q)
                | Q(addresses__city__icontains=q)
            ).distinct()
        items = items.order_by(values["order"] or "name", "pk")
    else:
        items = items.none()
    data.update(
        {
            "search_form": form,
            "page": Paginator(items, 25).get_page(request.GET.get("page")),
            "pagination_query": urlencode(
                {key: request.GET[key][:200] for key in form.fields if request.GET.get(key)}
            ),
        }
    )
    return render(request, "addressbook/list.html", data)


@secure_page
@login_required
@require_GET
def detail(request, site_id, pk):
    data = context(request, site_id)
    entry = get_object_or_404(entries(data["site"]), pk=pk)
    data.update(
        {
            "entry": entry,
            "possible_duplicates": entries(data["site"])
            .filter(
                kind=entry.kind,
                name__iexact=entry.name,
                first_name__iexact=entry.first_name,
            )
            .exclude(pk=entry.pk)[:5],
            "contacts": entry.contacts.all(),
            "addresses": entry.addresses.select_related("address_type"),
            "extra_info": entry.extra_info.select_related("info_type"),
            "employments": entry.employments.select_related("organization", "address"),
            "people": entry.people.select_related("person", "address").prefetch_related(
                "person__contacts"
            ),
        }
    )
    return render(request, "addressbook/detail.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, site_id, pk=None):
    data = context(request, site_id, "change" if pk else "add")
    status = 200
    with transaction.atomic():
        entry = (
            get_object_or_404(
                Entry.objects.select_for_update(),
                site=data["site"],
                pk=pk,
                archived_at__isnull=True,
            )
            if pk
            else Entry(site=data["site"])
        )
        version = entry.updated_at.isoformat() if pk else None
        form = EntryForm(
            request.POST if request.method == "POST" else None, site=data["site"], instance=entry
        )
        if request.method == "POST" and form.is_valid():
            if pk and form.cleaned_data["version"] != version:
                form.add_error(None, "Cette fiche a changé. Rechargez-la avant d’enregistrer.")
                status = 409
            else:
                saved = form.save(commit=False)
                if not pk:
                    saved.created_by = request.user
                saved.updated_by = request.user
                saved.save()
                audit(request, saved, "entry.update" if pk else "entry.create")
                messages.success(request, "Fiche privée enregistrée.")
                return redirect("addressbook:detail", site_id=site_id, pk=saved.pk)
    data.update({"form": form, "entry": entry if pk else None})
    return render(request, "addressbook/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def child_edit(request, site_id, pk, section, child_id=None):
    data = context(request, site_id, "change")
    if section not in CHILD_FORMS:
        raise Http404
    model, form_class, title = CHILD_FORMS[section]
    status = 200
    with transaction.atomic():
        entry = get_object_or_404(
            Entry.objects.select_for_update(), site=data["site"], pk=pk, archived_at__isnull=True
        )
        relation = "person" if model is Employment else "entry"
        if model is Employment and entry.kind != Entry.Kind.PERSON:
            raise Http404
        child = (
            get_object_or_404(model.objects.select_for_update(), pk=child_id, **{relation: entry})
            if child_id
            else model(**{relation: entry})
        )
        form = form_class(
            request.POST if request.method == "POST" else None, instance=child, entry=entry
        )
        # Lock the organization before validating/saving a relation. Organization
        # archival and address edits take this same parent lock.
        if model is Employment and request.method == "POST":
            try:
                organization_id = int(request.POST.get("organization", ""))
            except (TypeError, ValueError):
                organization_id = 0
            if 0 < organization_id <= 9223372036854775807:
                list(
                    Entry.objects.select_for_update().filter(site=data["site"], pk=organization_id)
                )
        if request.method == "POST" and form.is_valid():
            if not current_version(form, entry):
                status = 409
            else:
                saved = form.save(commit=False)
                if getattr(saved, "is_primary", False):
                    previous = model.objects.filter(entry=entry, is_primary=True).exclude(
                        pk=saved.pk
                    )
                    if section == "contacts":
                        previous = previous.filter(mode=saved.mode)
                    previous.update(is_primary=False)
                saved.save()
                entry.updated_by = request.user
                entry.save(update_fields=["updated_by", "updated_at"])
                audit(
                    request,
                    entry,
                    f"{model._meta.model_name}.update"
                    if child_id
                    else f"{model._meta.model_name}.create",
                    saved,
                )
                messages.success(
                    request,
                    f"{title} enregistrée."
                    if section != "employments"
                    else "Rattachement enregistré.",
                )
                return redirect("addressbook:detail", site_id=site_id, pk=pk)
    data.update({"entry": entry, "form": form, "section_title": title})
    return render(request, "addressbook/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, site_id, pk, restore=False):
    data = context(request, site_id, "restore" if restore else "delete")
    status = 200
    with transaction.atomic():
        entry = get_object_or_404(
            Entry.objects.select_for_update(),
            site=data["site"],
            pk=pk,
            archived_at__isnull=not restore,
        )
        form = VersionForm(
            request.POST if request.method == "POST" else None,
            initial={"version": entry.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if not current_version(form, entry):
                status = 409
            elif not restore and entry.people.filter(ends_on__isnull=True).exists():
                form.add_error(
                    None,
                    "Clôturez les rattachements professionnels avant d’archiver l’organisation.",
                )
            else:
                entry.archived_at = None if restore else timezone.now()
                entry.updated_by = request.user
                entry.save(update_fields=["archived_at", "updated_by", "updated_at"])
                audit(request, entry, "entry.restore" if restore else "entry.archive")
                messages.success(
                    request,
                    "Fiche restaurée."
                    if restore
                    else "Fiche archivée ; ses données sont conservées.",
                )
                return redirect("addressbook:detail", site_id=site_id, pk=pk)
    data.update({"entry": entry, "form": form, "restore": restore})
    return render(request, "addressbook/lifecycle.html", data, status=status)


@secure_page
@login_required
@require_GET
def type_list(request, site_id):
    data = context(request, site_id)
    if not data["can_manage_types"]:
        raise Http404
    data["types"] = EntryType.objects.filter(site=data["site"])
    return render(request, "addressbook/types.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def type_edit(request, site_id, pk=None):
    data = context(request, site_id)
    if not data["can_manage_types"]:
        raise Http404
    with transaction.atomic():
        entry_type = (
            get_object_or_404(EntryType.objects.select_for_update(), pk=pk, site=data["site"])
            if pk
            else EntryType(site=data["site"])
        )
        form = EntryTypeForm(
            request.POST if request.method == "POST" else None,
            site=data["site"],
            instance=entry_type,
        )
        if request.method == "POST" and form.is_valid():
            saved = form.save()
            AuditEvent.objects.create(
                actor=request.user,
                site=data["site"],
                action="addressbook.entry_type.update" if pk else "addressbook.entry_type.create",
                entity_type="addressbook.entrytype",
                entity_id=str(saved.pk),
            )
            messages.success(request, "Type de fiche enregistré.")
            return redirect("addressbook:types", site_id=site_id)
    data.update({"form": form, "entry_type": entry_type if pk else None})
    return render(request, "addressbook/type_form.html", data)
