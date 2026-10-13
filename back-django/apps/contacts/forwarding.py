import hashlib
import json
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.cobweb.views import secure_page
from apps.configuration.communications import smtp_connection

from .forms import ForwardForm, VersionForm
from .models import ContactRequest, Forward, Notification
from .reception import MAX_QUEUED_NOTIFICATIONS
from .security import policy_for
from .views import audit, context, lock_site


def forward_body(contact, introduction=""):
    parts = []
    if introduction:
        parts.extend([introduction, ""])
    parts.extend(
        [
            "Demande de contact retransmise depuis ViBix",
            f"Référence : {contact.reference}",
            f"Reçue le : {contact.created_at.isoformat()}",
            f"Nom : {contact.name} {contact.first_name}".rstrip(),
            f"Courriel : {contact.email}",
            f"Téléphone : {contact.phone or 'Non renseigné'}",
            f"Organisation : {contact.organization or 'Non renseignée'}",
            f"Objet : {contact.title}",
            "",
            "Message reçu :",
            contact.message,
        ]
    )
    if contact.form_snapshot:
        parts.extend(["", f"Formulaire : {contact.form_snapshot['label']} ({contact.form_type})"])
        for question in contact.form_snapshot.get("questions", []):
            parts.extend(
                ["", question["label"], contact.answers.get(question["key"], "") or "Non renseigné"]
            )
    return "\n".join(parts)


def pending_count(site):
    return sum(
        model.objects.filter(contact__site=site, state__in=["pending", "processing"]).count()
        for model in (Forward, Notification)
    )


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def forward(request, site_id, pk):
    data = context(request, site_id, "forward_contactrequest")
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            lock_site(request, site_id, "forward_contactrequest")
        contact = get_object_or_404(
            ContactRequest.objects.select_for_update(), site=data["site"], pk=pk
        )
        policy = policy_for(contact.site)
        form = ForwardForm(
            request.POST if request.method == "POST" else None,
            initial={
                "version": contact.updated_at.isoformat(),
                "submission_id": uuid4(),
                "recipient": policy.contact_recipient,
            },
        )
        if request.method == "POST" and form.is_valid():
            values = form.cleaned_data
            body = forward_body(contact, values["introduction"])
            payload_hash = hashlib.sha256(
                json.dumps([contact.pk, values["recipient"], body]).encode()
            ).hexdigest()
            existing = Forward.objects.filter(submission_id=values["submission_id"]).first()
            if existing:
                if (
                    existing.contact_id == contact.pk
                    and existing.payload_hash == payload_hash
                    and existing.requested_by_id == request.user.pk
                ):
                    return redirect("contacts:detail", site_id=site_id, pk=pk)
                form.add_error(None, "Ce formulaire a déjà servi à une autre retransmission.")
                status = 409
            elif contact.deleted_at or contact.status == "spam":
                form.add_error(
                    None, "Restaurez et validez une demande suspecte avant de la retransmettre."
                )
            elif values["version"] != contact.updated_at.isoformat():
                form.add_error(
                    None, "La demande a changé. Rechargez l’aperçu avant de retransmettre."
                )
                status = 409
            elif pending_count(contact.site) >= MAX_QUEUED_NOTIFICATIONS:
                form.add_error(
                    None, "La file d’envoi du site est pleine. Réessayez après son traitement."
                )
            else:
                try:
                    smtp_connection(policy)  # Validate configuration; does not connect or send.
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    job = Forward(
                        contact=contact,
                        requested_by=request.user,
                        submission_id=values["submission_id"],
                        recipient=values["recipient"],
                        body=body,
                        payload_hash=payload_hash,
                    )
                    job.full_clean(validate_unique=False)
                    try:
                        with transaction.atomic():
                            job.save()
                    except IntegrityError:
                        if not Forward.objects.filter(submission_id=job.submission_id).exists():
                            raise
                        form.add_error(
                            None, "Ce formulaire a déjà servi à une autre retransmission."
                        )
                        status = 409
                    else:
                        contact.save(update_fields=["updated_at"])
                        audit(request, contact, "forward.queued")
                        messages.success(request, "Retransmission enregistrée dans la file SMTP.")
                        return redirect("contacts:detail", site_id=site_id, pk=pk)
        data.update(
            contact=contact,
            form=form,
            preview=forward_body(
                contact, form.cleaned_data.get("introduction", "") if form.is_bound else ""
            ),
        )
    return render(request, "contacts/forward.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def retry(request, site_id, pk, forward_id):
    data = context(request, site_id, "forward_contactrequest")
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            lock_site(request, site_id, "forward_contactrequest")
        contact = get_object_or_404(
            ContactRequest.objects.select_for_update(), site=data["site"], pk=pk
        )
        job = get_object_or_404(Forward.objects.select_for_update(), contact=contact, pk=forward_id)
        form = VersionForm(
            request.POST if request.method == "POST" else None,
            initial={"version": contact.updated_at.isoformat()},
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != contact.updated_at.isoformat():
                form.add_error(None, "La demande a changé. Rechargez la page.")
                status = 409
            elif contact.deleted_at or contact.status == "spam" or job.state != "failed":
                form.add_error(None, "Cette retransmission ne peut pas être relancée.")
            elif pending_count(contact.site) >= MAX_QUEUED_NOTIFICATIONS:
                form.add_error(
                    None, "La file d’envoi du site est pleine. Réessayez après son traitement."
                )
            else:
                try:
                    smtp_connection(policy_for(contact.site))
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    job.state, job.attempts, job.lease = "pending", 0, None
                    job.next_attempt_at = timezone.now()
                    job.requested_by = request.user
                    job.save(
                        update_fields=[
                            "state",
                            "attempts",
                            "lease",
                            "next_attempt_at",
                            "requested_by",
                        ]
                    )
                    contact.save(update_fields=["updated_at"])
                    audit(request, contact, "forward.retry")
                    return redirect("contacts:detail", site_id=site_id, pk=pk)
        data.update(contact=contact, form=form, job=job, preview=job.body)
    return render(request, "contacts/forward.html", data, status=status)
