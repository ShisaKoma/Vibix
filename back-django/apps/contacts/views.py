import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.addressbook.models import Entry
from apps.audit.models import AuditEvent
from apps.cobweb.views import secure_page
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .addressbook import StaleLink, candidates, link_contact
from .api import public_site, quota_response
from .definitions import available_forms, schema_for
from .forms import (
    ClientForm,
    ContactSearchForm,
    LinkForm,
    SubmissionForm,
    TreatmentForm,
    VersionForm,
)
from .models import APIClient, ContactNote, ContactRequest, Notification
from .reception import (
    FormChanged,
    ReceiptConflict,
    ReceptionClosed,
    enqueue_notification,
    receive,
    suspend_notification,
)
from .security import challenge_receipt, consume_quota, digest, issue_challenge, policy_for

TRANSITIONS = {
    "new": {"new", "accepted", "spam"},
    "spam": {"spam", "accepted"},
    "accepted": {"accepted", "in_progress", "closed", "spam"},
    "in_progress": {"in_progress", "closed", "spam"},
    "closed": {"closed", "in_progress", "spam"},
}


def context(request, site_id, permission="view_contactrequest"):
    site = get_object_or_404(Site.objects.for_user(request.user).exclude(pk=1), pk=site_id)
    for code in {"view_contactrequest", permission}:
        if not has_site_permission(request.user, site, f"contacts.{code}"):
            raise Http404
    return {
        "site": site,
        "contacts_active": True,
        "can_change": has_site_permission(request.user, site, "contacts.change_contactrequest"),
        "can_delete": has_site_permission(request.user, site, "contacts.delete_contactrequest"),
        "can_restore": has_site_permission(request.user, site, "contacts.restore_contactrequest"),
        "can_link": has_site_permission(request.user, site, "contacts.link_addressbook")
        and has_site_permission(request.user, site, "addressbook.view_entry"),
        "can_forward": has_site_permission(request.user, site, "contacts.forward_contactrequest"),
        "can_manage_clients": has_site_permission(
            request.user, site, "contacts.manage_api_clients"
        ),
    }


def lock_site(request, site_id, permission):
    site = Site.objects.select_for_update(no_key=True).get(pk=site_id)
    for code in {"view_contactrequest", permission}:
        if not has_site_permission(request.user, site, f"contacts.{code}"):
            raise Http404
    return site


def audit(request, contact, action):
    AuditEvent.objects.create(
        actor=request.user,
        site=contact.site,
        action=f"contacts.{action}",
        entity_type="contacts.contactrequest",
        entity_id=str(contact.pk),
    )


@secure_page
@require_http_methods(["GET", "POST"])
def public_form(request, site_id):
    site = public_site(site_id)
    policy = policy_for(site)
    if request.method == "GET":
        allowed, delay = consume_quota(request, site, challenge=True)
        if not allowed:
            return quota_response(delay)
        schema = schema_for(site, request.GET.get("type", ""))
        challenge = issue_challenge(request, site, schema)["challenge"]
        form = SubmissionForm(
            initial={"challenge": challenge}, max_length=policy.contact_max_length, schema=schema
        )
    else:
        allowed, delay = consume_quota(request, site)
        if not allowed:
            return quota_response(delay)
        if request.content_type != "application/x-www-form-urlencoded":
            raise Http404
        receipt = challenge_receipt(request, site, request.POST.get("challenge", ""))
        schema = schema_for(site, request.POST.get("form_type", ""), receipt=receipt)
        form = SubmissionForm(request.POST, max_length=policy.contact_max_length, schema=schema)
        allowed_fields = set(form.fields) | {"csrfmiddlewaretoken"}
        if set(request.POST) - allowed_fields or any(
            len(request.POST.getlist(key)) != 1 for key in request.POST
        ):
            form.add_error(None, "La demande comporte des champs invalides.")
        if form.is_valid():
            receipt = challenge_receipt(
                request,
                site,
                form.cleaned_data.get("challenge", ""),
                form_type=form.cleaned_data["form_type"],
                form_version=form.cleaned_data["form_version"],
            )
            if receipt:
                try:
                    receive(site, form.cleaned_data, receipt)
                except FormChanged:
                    form.add_error(
                        None,
                        "Le formulaire a changé. Rechargez la page pour vérifier les questions.",
                    )
                except ReceiptConflict:
                    form.add_error(
                        None,
                        "Cette demande a déjà été soumise avec un autre contenu. "
                        "Rechargez la page.",
                    )
                except ReceptionClosed as error:
                    raise Http404 from error
                except ValidationError:
                    form.add_error(None, "La demande n’a pas pu être validée. Vérifiez les champs.")
                else:
                    return render(
                        request,
                        "contacts/public.html",
                        {
                            "site": site,
                            "accepted": True,
                            "schema": schema,
                            "retention_days": policy.contact_retention_days,
                        },
                        status=202,
                    )
            else:
                form.add_error(
                    None,
                    "Le formulaire a expiré ou a été envoyé trop rapidement. "
                    "Rechargez la page puis réessayez.",
                )
    return render(
        request,
        "contacts/public.html",
        {
            "site": site,
            "form": form,
            "schema": schema,
            "available_forms": available_forms(site),
            "retention_days": policy.contact_retention_days,
        },
    )


@secure_page
@login_required
@require_GET
def contact_list(request, site_id):
    data = context(request, site_id)
    inputs = {"state": "active", "order": "-created_at", **request.GET.dict()}
    inputs["q"] = inputs.get("q", "")[:200]
    form = ContactSearchForm(inputs, site=data["site"])
    if not form.is_valid():
        raise Http404
    values = form.cleaned_data
    state, status, q = values["state"], values["status"], values["q"]
    contacts = ContactRequest.objects.filter(site=data["site"], deleted_at__isnull=state != "trash")
    if status:
        contacts = contacts.filter(status=status)
    if q:
        contacts = contacts.filter(
            Q(name__icontains=q)
            | Q(first_name__icontains=q)
            | Q(email__icontains=q)
            | Q(title__icontains=q)
        )
    if values["form_type"] == "__untyped__":
        contacts = contacts.filter(form_type="")
    elif values["form_type"]:
        contacts = contacts.filter(form_type=values["form_type"])
    category = values["category"]
    if category == "none":
        contacts = contacts.filter(category__isnull=True)
    elif category:
        contacts = contacts.filter(category_id=category)
    contacts = contacts.select_related("category").order_by(values["order"], "-pk")
    data.update(
        {
            "search_form": form,
            "page_query": urlencode({k: v for k, v in values.items() if v}),
            "page": Paginator(contacts, 25).get_page(request.GET.get("page")),
            "status": status,
            "state": state,
            "q": q,
            "statuses": ContactRequest.Status.choices,
        }
    )
    return render(request, "contacts/list.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def detail(request, site_id, pk):
    data = context(
        request,
        site_id,
        "change_contactrequest" if request.method == "POST" else "view_contactrequest",
    )
    status_code = 200
    with transaction.atomic():
        if request.method == "POST":
            lock_site(request, site_id, "change_contactrequest")
        contact = get_object_or_404(
            ContactRequest.objects.select_for_update(), site=data["site"], pk=pk
        )
        data["received_answers"] = [
            {"label": question["label"], "value": contact.answers.get(question["key"], "")}
            for question in contact.form_snapshot.get("questions", [])
        ]
        form = TreatmentForm(
            request.POST if request.method == "POST" else None,
            contact=contact,
            initial={
                "version": contact.updated_at.isoformat(),
                "status": contact.status,
                "category": contact.category_id,
            },
        )
        form.fields["status"].choices = [
            (key, label)
            for key, label in ContactRequest.Status.choices
            if key in TRANSITIONS[contact.status]
        ]
        if request.method == "POST" and form.is_valid():
            if contact.deleted_at:
                form.add_error(None, "Restaurez la demande avant de la modifier.")
            elif form.cleaned_data["version"] != contact.updated_at.isoformat():
                form.add_error(None, "Cette demande a changé. Rechargez-la avant d’enregistrer.")
                status_code = 409
            else:
                contact.status = form.cleaned_data["status"]
                if "category" in request.POST:
                    contact.category = form.cleaned_data["category"]
                contact.full_clean()
                contact.save()
                if form.cleaned_data["note"]:
                    ContactNote.objects.create(
                        contact=contact, author=request.user, text=form.cleaned_data["note"]
                    )
                if contact.status == "spam":
                    suspend_notification(contact)
                else:
                    enqueue_notification(contact)
                audit(request, contact, "treat")
                messages.success(request, "Demande enregistrée.")
                return redirect("contacts:detail", site_id=site_id, pk=pk)
    notification = Notification.objects.filter(contact=contact).first()
    data.update(
        {
            "contact": contact,
            "form": form,
            "notes": contact.notes.select_related("author"),
            "notification": notification,
            "forwards": contact.forwards.select_related("requested_by")[:50],
            "retry_form": VersionForm(initial={"version": contact.updated_at.isoformat()}),
        }
    )
    return render(request, "contacts/detail.html", data, status=status_code)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, site_id, pk, restore=False):
    data = context(
        request, site_id, "restore_contactrequest" if restore else "delete_contactrequest"
    )
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            lock_site(
                request, site_id, "restore_contactrequest" if restore else "delete_contactrequest"
            )
        contact = get_object_or_404(
            ContactRequest.objects.select_for_update(), site=data["site"], pk=pk
        )
        form = VersionForm(
            request.POST if request.method == "POST" else None,
            initial={"version": contact.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != contact.updated_at.isoformat():
                form.add_error(None, "La demande a changé. Rechargez-la.")
                status = 409
            elif bool(contact.deleted_at) == restore:
                contact.deleted_at = None if restore else timezone.now()
                contact.save()
                enqueue_notification(contact) if restore else suspend_notification(contact)
                audit(request, contact, "restore" if restore else "archive")
                return redirect("contacts:detail", site_id=site_id, pk=pk)
            else:
                form.add_error(None, "Cette action a déjà été effectuée.")
    data.update(
        {
            "contact": contact,
            "form": form,
            "action_label": "Restaurer" if restore else "Mettre à la corbeille",
        }
    )
    return render(request, "contacts/confirm.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["POST"])
def retry_notification(request, site_id, pk):
    data = context(request, site_id, "change_contactrequest")
    with transaction.atomic():
        lock_site(request, site_id, "change_contactrequest")
        contact = get_object_or_404(
            ContactRequest.objects.select_for_update(), site=data["site"], pk=pk
        )
        form = VersionForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "contacts/confirm.html",
                {
                    **data,
                    "contact": contact,
                    "form": form,
                    "action_label": "Relancer la notification",
                },
                status=400,
            )
        if form.cleaned_data["version"] != contact.updated_at.isoformat():
            return render(
                request,
                "contacts/confirm.html",
                {
                    **data,
                    "contact": contact,
                    "form": form,
                    "action_label": "La demande a changé : rechargez-la",
                },
                status=409,
            )
        if contact.status == "spam" or contact.deleted_at:
            raise Http404
        enqueue_notification(contact, retry=True)
        contact.save()
        audit(request, contact, "notification.retry")
    return redirect("contacts:detail", site_id=site_id, pk=pk)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def addressbook_link(request, site_id, pk):
    data = context(request, site_id, "link_addressbook")
    if not data["can_link"]:
        raise Http404
    contact = get_object_or_404(ContactRequest, site=data["site"], pk=pk, deleted_at__isnull=True)
    if contact.status not in {"accepted", "in_progress", "closed"}:
        raise Http404
    target = None
    if request.GET.get("entry"):
        try:
            target_id = int(request.GET["entry"])
        except ValueError as error:
            raise Http404 from error
        if not 0 < target_id <= 9223372036854775807:
            raise Http404
        target = get_object_or_404(
            Entry,
            site=data["site"],
            kind="person",
            is_active=True,
            archived_at__isnull=True,
            pk=target_id,
        )
    form = LinkForm(
        request.POST if request.method == "POST" else None,
        site=data["site"],
        initial={
            "version": contact.updated_at.isoformat(),
            "mode": "link" if target else "create",
            "entry": target,
            "entry_version": target.updated_at.isoformat() if target else "",
        },
    )
    permitted_modes = {"link"}
    if has_site_permission(request.user, data["site"], "addressbook.add_entry"):
        permitted_modes.add("create")
    if (
        has_site_permission(request.user, data["site"], "addressbook.change_entry")
        and policy_for(data["site"]).contact_addressbook_mode != "create"
    ):
        permitted_modes.add("sync")
    if (
        request.method == "POST"
        and request.POST.get("mode") in {"create", "sync"} - permitted_modes
    ):
        raise Http404
    form.fields["mode"].choices = [
        (key, label) for key, label in form.fields["mode"].choices if key in permitted_modes
    ]
    if form.initial["mode"] not in permitted_modes:
        form.initial["mode"] = "link"
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            link_contact(request.user, contact.pk, site_id, form.cleaned_data)
        except StaleLink:
            form.add_error(
                None,
                "La demande ou la fiche a changé, ou une liaison existe déjà. Rechargez l’aperçu.",
            )
            status = 409
        except (PermissionDenied, Entry.DoesNotExist) as error:
            raise Http404 from error
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(request, "Liaison au carnet enregistrée.")
            return redirect("contacts:detail", site_id=site_id, pk=pk)
    if target:
        form.fields["entry"].widget = form.fields["entry_version"].widget.__class__()
    data.update(
        {
            "contact": contact,
            "form": form,
            "matches": candidates(contact),
            "target": target,
            "target_contacts": target.contacts.all() if target else [],
            "people": form.fields["entry"].queryset,
            "policy": policy_for(data["site"]),
        }
    )
    return render(request, "contacts/addressbook.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def api_clients(request, site_id):
    data = context(request, site_id, "manage_api_clients")
    form = ClientForm(request.POST if request.method == "POST" else None)
    token = None
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            site = lock_site(request, site_id, "manage_api_clients")
            if (
                APIClient.objects.filter(
                    site=site, revoked_at__isnull=True, expires_at__gt=timezone.now()
                ).count()
                >= 20
            ):
                form.add_error(
                    None,
                    "Ce site possède déjà 20 clients actifs. "
                    "Révoquez un accès avant d’en créer un autre.",
                )
            elif APIClient.objects.filter(
                site=site, name__iexact=form.cleaned_data["name"]
            ).exists():
                form.add_error("name", "Un client porte déjà ce nom. Choisissez un autre nom.")
            else:
                token = "ct_" + secrets.token_urlsafe(32)
                client = APIClient.objects.create(
                    site=site,
                    name=form.cleaned_data["name"],
                    token_hash=digest(token),
                    expires_at=timezone.now()
                    + timedelta(days=form.cleaned_data["expires_in_days"]),
                )
                AuditEvent.objects.create(
                    actor=request.user,
                    site=site,
                    action="contacts.client.create",
                    entity_type="contacts.apiclient",
                    entity_id=str(client.pk),
                )
                form = ClientForm()
    data.update(
        {
            "form": form,
            "clients": APIClient.objects.filter(site=data["site"])
            .annotate(
                active_order=Case(
                    When(revoked_at__isnull=True, expires_at__gt=timezone.now(), then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("active_order", "-created_at")[:100],
            "token": token,
        }
    )
    return render(request, "contacts/clients.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def revoke_client(request, site_id, pk):
    data = context(request, site_id, "manage_api_clients")
    with transaction.atomic():
        if request.method == "POST":
            lock_site(request, site_id, "manage_api_clients")
        client = get_object_or_404(APIClient.objects.select_for_update(), site=data["site"], pk=pk)
        form = VersionForm(
            request.POST if request.method == "POST" else None, initial={"version": str(client.pk)}
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != str(client.pk):
                raise Http404
            if not client.revoked_at:
                client.revoked_at = timezone.now()
                client.save(update_fields=["revoked_at"])
                AuditEvent.objects.create(
                    actor=request.user,
                    site=client.site,
                    action="contacts.client.revoke",
                    entity_type="contacts.apiclient",
                    entity_id=str(client.pk),
                )
            return redirect("contacts:clients", site_id=site_id)
    data.update({"form": form, "client": client, "action_label": "Révoquer ce client API"})
    return render(request, "contacts/confirm.html", data)
