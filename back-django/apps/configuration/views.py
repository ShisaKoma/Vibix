from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.availability import active_function_codes
from apps.core.navigation import COBWEB_FUNCTIONS, UI_FUNCTIONS
from apps.tenancy.forms import SiteAvailabilityForm, SiteSettingsForm
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .communications import smtp_password_configured
from .forms import (
    CONFIGURABLE_MODULES,
    LookupEntryFormSet,
    LookupListForm,
    SiteContactPolicyForm,
    SiteSMTPForm,
)
from .models import LIST_KIND_CHOICES, LookupList, SiteCommunicationSettings

MANAGE_PERMISSION = "tenancy.manage_site_settings"


def manageable_site(request, site_id):
    site = get_object_or_404(Site.objects.for_user(request.user), pk=site_id)
    if not has_site_permission(request.user, site, MANAGE_PERMISSION):
        raise Http404
    return site


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def site_settings(request, site_id):
    site = manageable_site(request, site_id)
    form = SiteSettingsForm(instance=site)
    availability_form = SiteAvailabilityForm(instance=site)
    communication = SiteCommunicationSettings.objects.filter(site=site).first()
    communication = communication or SiteCommunicationSettings(site=site)
    smtp_form = SiteSMTPForm(instance=communication, prefix="smtp")
    contact_form = SiteContactPolicyForm(instance=communication, prefix="contact")
    response_status = 200
    if request.method == "POST":
        with transaction.atomic():
            site = Site.objects.select_for_update().get(pk=site.pk)
            section = request.POST.get("settings_section", "identity")
            if section in {"smtp", "contact"}:
                communication = SiteCommunicationSettings.objects.filter(site=site).first()
                communication = communication or SiteCommunicationSettings(site=site)
                form_class = SiteSMTPForm if section == "smtp" else SiteContactPolicyForm
                communication_form = form_class(
                    request.POST, instance=communication, prefix=section
                )
                if section == "smtp":
                    smtp_form = communication_form
                else:
                    contact_form = communication_form
                if communication_form.is_valid():
                    communication_form.save()
                    AuditEvent.objects.create(
                        actor=request.user,
                        site=site,
                        action=f"settings.site.{section}.update",
                        entity_type="configuration.sitecommunicationsettings",
                        entity_id=str(communication.pk),
                    )
                    messages.success(request, "Réglages enregistrés.")
                    return redirect("configuration:site-settings", site_id=site.pk)
                expected = communication.updated_at.isoformat() if communication.pk else "new"
                response_status = 409 if request.POST.get(f"{section}-version") != expected else 200
            elif section == "availability":
                availability_form = SiteAvailabilityForm(request.POST, instance=site)
                if availability_form.is_valid():
                    if availability_form.has_changed():
                        availability_form.save()
                        AuditEvent.objects.create(
                            actor=request.user,
                            site=site,
                            action="settings.site.availability.update",
                            entity_type="tenancy.site",
                            entity_id=str(site.pk),
                        )
                    messages.success(request, "Disponibilité des modules enregistrée.")
                    return redirect("configuration:site-settings", site_id=site.pk)
            elif section == "identity":
                original_icon = site.icon.name
                form = SiteSettingsForm(request.POST, request.FILES, instance=site)
                if form.is_valid():
                    try:
                        form.save()
                        AuditEvent.objects.create(
                            actor=request.user,
                            site=site,
                            action="settings.site.update",
                            entity_type="tenancy.site",
                            entity_id=str(site.pk),
                        )
                    except Exception:
                        if site.icon.name and site.icon.name != original_icon:
                            site.icon.storage.delete(site.icon.name)
                        raise
                    messages.success(request, "Les paramètres du site ont été enregistrés.")
                    return redirect("configuration:site-settings", site_id=site.pk)
            else:
                raise Http404
    active_cobweb_functions = active_function_codes(site)
    return render(
        request,
        "configuration/site_settings.html",
        {
            "site": site,
            "kinds": LIST_KIND_CHOICES,
            "form": form,
            "availability_form": availability_form,
            "smtp_form": smtp_form,
            "contact_form": contact_form,
            "smtp_password_configured": smtp_password_configured(site.pk),
            "cobweb_functions": [
                {
                    "label": UI_FUNCTIONS[key]["label"],
                    "active": site.cobweb_enabled and code in active_cobweb_functions,
                }
                for code, key in COBWEB_FUNCTIONS.items()
            ],
        },
        status=response_status,
    )


@login_required
@require_http_methods(["GET", "POST"])
def lookup_lists(request, site_id, kind):
    site = manageable_site(request, site_id)
    if kind not in dict(LIST_KIND_CHOICES):
        raise Http404
    module = request.GET.get("module", "")
    if module and module not in dict(CONFIGURABLE_MODULES):
        raise Http404
    lists = (
        LookupList.objects.filter(site=site, kind=kind)
        .select_related("site")
        .prefetch_related("entries")
    )
    if module:
        lists = lists.filter(module=module)
    form = LookupListForm(request.POST or None, site=site, kind=kind)
    if module and request.method == "GET":
        form.initial["module"] = module
    form.instance.kind = kind
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.site = site
        with transaction.atomic():
            item.save()
            AuditEvent.objects.create(
                actor=request.user,
                site=site,
                action="settings.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(item.pk),
            )
        return redirect("configuration:lookup-edit", site_id=site.pk, list_id=item.pk)
    return render(
        request,
        "configuration/lookup_lists.html",
        {
            "site": site,
            "kind": kind,
            "kind_label": dict(LIST_KIND_CHOICES)[kind],
            "lists": lists,
            "form": form,
            "module": module,
            "module_filters": CONFIGURABLE_MODULES,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def lookup_edit(request, site_id, list_id):
    site = manageable_site(request, site_id)
    item = get_object_or_404(LookupList, pk=list_id, site=site)
    form = LookupListForm(request.POST or None, instance=item)
    entries = LookupEntryFormSet(request.POST or None, instance=item, prefix="entries")
    if request.method == "POST" and form.is_valid() and entries.is_valid():
        with transaction.atomic():
            form.save()
            entries.save()
            AuditEvent.objects.create(
                actor=request.user,
                site=site,
                action="settings.reference.update",
                entity_type="configuration.lookuplist",
                entity_id=str(item.pk),
            )
        return redirect(request.path)
    return render(
        request,
        "configuration/lookup_edit.html",
        {"site": site, "item": item, "form": form, "entries": entries},
    )
