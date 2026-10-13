from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.views import secure_page
from apps.configuration.views import manageable_site
from apps.tenancy.models import Site

from .definition_forms import DefinitionForm
from .models import ContactFormDefinition


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def definitions(request, site_id, pk=None, create=False):
    site = manageable_site(request, site_id)
    if site.pk == 1:
        raise Http404
    editing = create or pk is not None
    if request.method == "POST" and not editing:
        raise Http404
    with transaction.atomic():
        if request.method == "POST":
            Site.objects.select_for_update(no_key=True).get(pk=site.pk)
            site = manageable_site(request, site_id)
        instance = (
            get_object_or_404(ContactFormDefinition, site=site, pk=pk)
            if pk is not None
            else ContactFormDefinition(site=site)
        )
        form = (
            DefinitionForm(request.POST if request.method == "POST" else None, instance=instance)
            if editing
            else None
        )
        status = 200
        if request.method == "POST":
            expected = instance.updated_at.isoformat() if instance.pk else "new"
            if form.is_valid():
                definition = form.save()
                AuditEvent.objects.create(
                    actor=request.user,
                    site=site,
                    action="settings.contacts.form.create"
                    if create
                    else "settings.contacts.form.update",
                    entity_type="contacts.contactformdefinition",
                    entity_id=str(definition.pk),
                )
                messages.success(request, "Formulaire Contact enregistré.")
                return redirect("contacts:definitions", site_id=site.pk)
            if request.POST.get("version") != expected:
                status = 409
        return render(
            request,
            "contacts/definitions.html",
            {
                "site": site,
                "form": form,
                "definitions": ContactFormDefinition.objects.filter(site=site),
                "editing": editing,
            },
            status=status,
        )
