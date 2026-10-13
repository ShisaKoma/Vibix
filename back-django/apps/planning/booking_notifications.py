from datetime import timedelta
from email.utils import formataddr
from uuid import uuid4

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.configuration.communications import smtp_connection
from apps.configuration.models import SiteCommunicationSettings

from .booking_models import BookingNotification


def audit(job, state):
    AuditEvent.objects.create(
        site_id=job.booking.site_id,
        action=f"planning.bookingnotification.{state}",
        entity_type="planning.bookingnotification",
        entity_id=str(job.pk),
    )


def claim(site_id=None):
    now = timezone.now()
    with transaction.atomic():
        rows = (
            BookingNotification.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(state="processing", attempts__gte=5, next_attempt_at__lte=now)
            .select_related("booking")
        )
        if site_id:
            rows = rows.filter(booking__site_id=site_id)
        for job in rows.order_by("pk")[:100]:
            job.state, job.lease = "failed", None
            job.save(update_fields=["state", "lease"])
            audit(job, "failed")
    with transaction.atomic():
        rows = BookingNotification.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            Q(state="pending") | Q(state="processing", next_attempt_at__lte=now),
            next_attempt_at__lte=now,
            booking__site__is_active=True,
            booking__site__planning_enabled=True,
        )
        if site_id:
            rows = rows.filter(booking__site_id=site_id)
        job = rows.order_by("next_attempt_at", "pk").first()
        if not job:
            return None
        job.state, job.lease = "processing", uuid4()
        job.attempts += 1
        job.next_attempt_at = now + timedelta(minutes=2)
        job.save(update_fields=["state", "lease", "attempts", "next_attempt_at"])
        return job


def finish(job, state):
    with transaction.atomic():
        values = {"state": state, "lease": None}
        if state == "sent":
            values["sent_at"] = timezone.now()
        else:
            values["next_attempt_at"] = timezone.now() + timedelta(minutes=min(60, 2**job.attempts))
        changed = BookingNotification.objects.filter(
            pk=job.pk, lease=job.lease, state="processing"
        ).update(**values)
        if changed:
            audit(job, state)
    return state


def deliver(job):
    job = (
        BookingNotification.objects.select_related("booking__site")
        .filter(pk=job.pk, lease=job.lease, state="processing")
        .first()
    )
    if not job:
        return "superseded"
    booking = job.booking
    try:
        policy = SiteCommunicationSettings.objects.get(site_id=booking.site_id)
        connection = smtp_connection(policy)
        body = (
            f"La réservation interne {booking.reference} a été mise à jour "
            f"(opération version {job.event_version}).\n"
            "Consultez sa fiche pour l'état actuel.\n"
        )
        if settings.RESERVATIONS_ADMIN_BASE_URL:
            body += (
                settings.RESERVATIONS_ADMIN_BASE_URL
                + reverse("planning:booking-detail", args=[booking.site_id, booking.pk])
                + "\n"
            )
        mail = EmailMessage(
            subject="Mise à jour d'une réservation interne",
            body=body,
            from_email=formataddr((policy.smtp_from_name, policy.smtp_from_email)),
            to=[job.recipient],
            connection=connection,
            headers={
                "Message-ID": (
                    f"<booking-{booking.reference}-{job.event_version}@vibix.reservations>"
                )
            },
        )
        if mail.send() != 1:
            raise OSError("Notification non remise au relais.")
    except Exception:
        return finish(job, "failed" if job.attempts >= 5 else "pending")
    return finish(job, "sent")
