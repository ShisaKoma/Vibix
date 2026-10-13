import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.configuration.communications import is_contact_spam
from apps.tenancy.models import Site

from .definitions import schema_for
from .models import APIClient, ContactRequest, Forward, Notification
from .security import policy_for

PAYLOAD_FIELDS = ("name", "first_name", "email", "phone", "organization", "title", "message")
MAX_QUEUED_NOTIFICATIONS = 1000


class ReceiptConflict(Exception):
    pass


class FormChanged(Exception):
    pass


class ReceptionClosed(Exception):
    pass


@transaction.atomic
def receive(site, data, receipt_hash, *, client=None):
    site = Site.objects.select_for_update(no_key=True).get(pk=site.pk)
    if site.pk == 1 or not (
        site.is_active and site.public_access_enabled and site.contacts_enabled
    ):
        raise ReceptionClosed
    if (
        client
        and not APIClient.objects.select_for_update()
        .filter(pk=client.pk, site=site, revoked_at__isnull=True, expires_at__gt=timezone.now())
        .exists()
    ):
        raise ReceptionClosed
    policy = policy_for(site)
    payload = {key: data[key] for key in PAYLOAD_FIELDS}
    if len(payload["message"]) > policy.contact_max_length:
        raise ValidationError("Le message dépasse la limite du site.")
    existing = ContactRequest.objects.filter(site=site, receipt_hash=receipt_hash).first()
    try:
        schema = schema_for(site, data.get("form_type", ""), receipt=receipt_hash)
    except Http404 as error:
        raise ReceptionClosed from error
    answers = {question["key"]: data.get(question["key"], "") for question in schema["questions"]}
    hash_payload = {
        **payload,
        "form_type": data.get("form_type", schema["code"]),
        "form_version": data.get("form_version", schema["version"]),
        "answers": answers,
    }
    if existing and not existing.form_snapshot:
        if answers:
            raise ReceiptConflict
        hash_payload = payload
    payload_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if existing:
        if existing.payload_hash != payload_hash:
            raise ReceiptConflict
        return existing
    if data.get("form_version", schema["version"]) != schema["version"]:
        raise FormChanged
    spam = bool(data.get("website")) or is_contact_spam(
        policy,
        message=data["message"],
        title=data["title"],
        name=f"{data['name']} {data['first_name']}",
        email=data["email"],
        phone=data["phone"],
        organization=data["organization"],
    )
    spam = spam or any(is_contact_spam(policy, message=value) for value in answers.values())
    contact = ContactRequest(
        site=site,
        **payload,
        receipt_hash=receipt_hash,
        payload_hash=payload_hash,
        status="spam" if spam else "new",
        source="api" if client else "web",
        api_client=client,
        form_type=schema["code"],
        form_snapshot=schema,
        answers=answers,
    )
    contact.full_clean()
    contact.save()
    AuditEvent.objects.create(
        site=site,
        action="contacts.receive",
        entity_type="contacts.contactrequest",
        entity_id=str(contact.pk),
    )
    if not spam:
        enqueue_notification(contact)
    return contact


@transaction.atomic
def enqueue_notification(contact, *, retry=False):
    Site.objects.select_for_update(no_key=True).get(pk=contact.site_id)
    if contact.status == "spam" or contact.deleted_at:
        return
    job, created = Notification.objects.get_or_create(contact=contact)
    if not created and (job.state == "cancelled" or retry and job.state == "failed"):
        job.state, job.attempts, job.next_attempt_at, job.lease = "pending", 0, timezone.now(), None
        job.save(update_fields=["state", "attempts", "next_attempt_at", "lease"])
    if (
        job.state == "pending"
        and Notification.objects.filter(
            contact__site=contact.site, state__in=["pending", "processing"]
        ).count()
        + Forward.objects.filter(
            contact__site=contact.site, state__in=["pending", "processing"]
        ).count()
        > MAX_QUEUED_NOTIFICATIONS
    ):
        job.state = "failed"
        job.save(update_fields=["state"])


def suspend_notification(contact):
    Forward.objects.filter(contact=contact, state__in=["pending", "processing"]).update(
        state="cancelled", lease=None
    )
    Notification.objects.filter(contact=contact).exclude(state="sent").update(
        state="cancelled", lease=None
    )
