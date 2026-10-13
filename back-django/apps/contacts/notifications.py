import uuid
from datetime import timedelta
from email.utils import formataddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.configuration.communications import smtp_connection
from apps.tenancy.permissions import has_site_permission

from .models import ContactRequest, Forward, Notification
from .security import policy_for


def claim_notification(site_id=None):
    return claim_job(Notification, site_id)


def claim_forward(site_id=None):
    return claim_job(Forward, site_id)


def claim_job(model, site_id=None):
    now = timezone.now()
    with transaction.atomic():
        exhausted = model.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            state="processing", attempts__gte=5, next_attempt_at__lte=now
        )
        if site_id:
            exhausted = exhausted.filter(contact__site_id=site_id)
        for old in exhausted.select_related("contact").order_by("pk")[:100]:
            old.state, old.lease = "failed", None
            old.save(update_fields=["state", "lease"])
            AuditEvent.objects.create(
                site_id=old.contact.site_id,
                action=f"contacts.{model._meta.model_name}.failed",
                entity_type=f"contacts.{model._meta.model_name}",
                entity_id=str(old.pk),
            )
    with transaction.atomic():
        jobs = model.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            Q(state="pending") | Q(state="processing", next_attempt_at__lte=now),
            next_attempt_at__lte=now,
            contact__site__is_active=True,
            contact__site__contacts_enabled=True,
        )
        if site_id:
            jobs = jobs.filter(contact__site_id=site_id)
        # PostgreSQL: do not select_for_update a joined nullable relation.
        job = jobs.order_by("next_attempt_at", "pk").first()
        if not job:
            return None
        job.state, job.lease = "processing", uuid.uuid4()
        job.attempts += 1
        job.next_attempt_at = now + timedelta(minutes=2)
        job.save(update_fields=["state", "lease", "attempts", "next_attempt_at"])
        return job


def finish_notification(job, contact, state, **values):
    with transaction.atomic():
        model = type(job)
        changed = model.objects.filter(pk=job.pk, lease=job.lease, state="processing").update(
            state=state, lease=None, **values
        )
        if changed:
            AuditEvent.objects.create(
                site_id=contact.site_id,
                action=f"contacts.{model._meta.model_name}.{state}",
                entity_type=f"contacts.{model._meta.model_name}",
                entity_id=str(job.pk),
            )
    return state


def deliver_notification(job):
    return deliver_job(job)


def deliver_forward(job):
    return deliver_job(job)


def deliver_job(job):
    if not type(job).objects.filter(pk=job.pk, state="processing", lease=job.lease).exists():
        return "cancelled"
    contact = ContactRequest.objects.select_related("site").filter(pk=job.contact_id).first()
    if contact is None:
        return "cancelled"
    if (
        contact.deleted_at
        or contact.status == "spam"
        or not (contact.site.is_active and contact.site.contacts_enabled)
    ):
        return finish_notification(job, contact, "cancelled")
    if isinstance(job, Forward):
        actor = job.requested_by
        if actor is None or not all(
            has_site_permission(actor, contact.site, code)
            for code in ("contacts.view_contactrequest", "contacts.forward_contactrequest")
        ):
            return finish_notification(job, contact, "cancelled")
    try:
        policy = policy_for(contact.site)
        if not isinstance(job, Forward) and not policy.contact_recipient:
            raise ValidationError("Destinataire absent.")
        connection = smtp_connection(policy)
        # Fixed headers for both mail types; visitor input stays in the plain-text body.
        # No automatic reply to an unverified visitor address.
        body = "Une nouvelle demande de contact est disponible dans votre espace ViBix.\n"
        if settings.CONTACTS_ADMIN_BASE_URL:
            body += (
                settings.CONTACTS_ADMIN_BASE_URL
                + reverse("contacts:detail", args=[contact.site_id, contact.pk])
                + "\n"
            )
        message_id = job.submission_id if isinstance(job, Forward) else contact.reference
        mail = EmailMessage(
            subject="Demande de contact retransmise"
            if isinstance(job, Forward)
            else "Nouvelle demande de contact",
            body=job.body if isinstance(job, Forward) else body,
            from_email=formataddr((policy.smtp_from_name, policy.smtp_from_email)),
            to=[job.recipient if isinstance(job, Forward) else policy.contact_recipient],
            connection=connection,
            headers={"Message-ID": f"<{message_id}@vibix.contacts>"},
        )
        if mail.send() != 1:
            raise OSError("Notification non remise au relais.")
    except Exception:
        # No provider exception, credential, address or body reaches logs/UI.
        state = "failed" if job.attempts >= 5 else "pending"
        return finish_notification(
            job,
            contact,
            state,
            next_attempt_at=timezone.now() + timedelta(minutes=min(60, 2**job.attempts)),
        )
    return finish_notification(job, contact, "sent", sent_at=timezone.now())
