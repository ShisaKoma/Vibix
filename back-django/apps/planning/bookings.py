"""Disponibilités, occupation et transitions atomiques des réservations internes."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.products.pricing import tariff_data
from apps.products.publication import public_variant_queryset
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .booking_models import (
    Booking,
    BookingHistory,
    BookingNotification,
    BookingOffer,
    BookingWindow,
)
from .models import Event, Resource


class BookingConflict(ValidationError):
    pass


class QuoteChanged(BookingConflict):
    pass


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def offer_ready(offer):
    if not (
        offer.site.is_active
        and offer.site.products_enabled
        and offer.site.planning_enabled
        and offer.is_active
        and offer.calendar.is_active
        and offer.resource.is_active
        and offer.variant.product.is_reservable
    ):
        return False
    if (
        not public_variant_queryset(offer.site, require_public=False)
        .filter(pk=offer.variant_id)
        .exists()
    ):
        return False
    return not offer.tariff_id or bool(
        offer.tariff.is_active and offer.tariff.is_public and offer.tariff.archived_at is None
    )


def offer_queryset(site):
    return BookingOffer.objects.filter(site=site).select_related(
        "site", "calendar", "resource", "variant__product__site", "tariff"
    )


def quote_data(offer):
    product = offer.variant.product
    return {
        "offer_id": offer.pk,
        "offer_version": offer.version,
        "product_id": product.pk,
        "product_version": product.version,
        "product": product.title,
        "variant_id": offer.variant_id,
        "variant_version": offer.variant.version,
        "variant": offer.variant.title,
        "resource_id": offer.resource_id,
        "resource": offer.resource.name,
        "calendar_id": offer.calendar_id,
        "calendar": offer.calendar.name,
        "title": offer.title,
        "timezone": offer.calendar.timezone,
        "duration_minutes": offer.duration_minutes,
        "before_minutes": offer.before_minutes,
        "after_minutes": offer.after_minutes,
        "cancellation_minutes": offer.cancellation_minutes,
        "cancellation_terms": offer.cancellation_terms,
        "tariff": tariff_data(offer.tariff) if offer.tariff_id and offer.tariff.is_public else None,
        "confirmation": "manual",
        "quantity": 1,
    }


def issue_quote(offer):
    data = quote_data(offer)
    return data, signing.dumps(
        {"site": offer.site_id, "quote": fingerprint(data)}, salt="planning.booking.quote"
    )


def check_quote(offer, token):
    current = quote_data(offer)
    try:
        submitted = signing.loads(token, salt="planning.booking.quote", max_age=1800)
    except signing.BadSignature as error:
        raise QuoteChanged(
            "Le récapitulatif a expiré. Rechargez-le et confirmez à nouveau."
        ) from error
    if submitted != {"site": offer.site_id, "quote": fingerprint(current)}:
        raise QuoteChanged(
            "L'offre ou son tarif a changé. Rechargez le récapitulatif et confirmez à nouveau."
        )
    return current


def intervals(offer, start, snapshot=None):
    settings = snapshot or {
        key: getattr(offer, key) for key in ("duration_minutes", "before_minutes", "after_minutes")
    }
    start = start.astimezone(UTC)
    end = start + timedelta(minutes=settings["duration_minutes"])
    return (
        start,
        end,
        start - timedelta(minutes=settings["before_minutes"]),
        end + timedelta(minutes=settings["after_minutes"]),
    )


def validate_slot(offer, start, *, exclude=None, snapshot=None, now=None):
    now = now or timezone.now()
    if not timezone.is_aware(start):
        raise ValidationError("Le créneau doit inclure un fuseau explicite.")
    left, right, occupied_left, occupied_right = intervals(offer, start, snapshot)
    if (
        not now + timedelta(minutes=offer.lead_minutes)
        <= left
        <= now + timedelta(days=offer.horizon_days)
    ):
        raise BookingConflict("Ce créneau est hors des délais autorisés.")
    windows = BookingWindow.objects.filter(
        calendar=offer.calendar, resource=offer.resource, is_active=True
    )
    opening = windows.filter(
        is_closed=False, starts_at__lte=occupied_left, ends_at__gte=occupied_right
    ).order_by("starts_at", "pk")
    if not any(
        (left - row.starts_at.astimezone(UTC)).total_seconds() % (offer.step_minutes * 60) == 0
        for row in opening
    ):
        raise BookingConflict(
            "Le créneau n'est pas dans une plage ouverte ou ne respecte pas son pas."
        )
    if windows.filter(
        is_closed=True, starts_at__lt=occupied_right, ends_at__gt=occupied_left
    ).exists():
        raise BookingConflict("Une fermeture ou indisponibilité couvre ce créneau.")
    conflicts = Booking.objects.filter(
        resource=offer.resource,
        state__in=Booking.OCCUPYING,
        occupied_starts_at__lt=occupied_right,
        occupied_ends_at__gt=occupied_left,
    )
    if exclude:
        conflicts = conflicts.exclude(pk=exclude)
    if (
        conflicts.exists()
        or Event.objects.filter(
            site=offer.site,
            resources=offer.resource,
            deleted_at__isnull=True,
            starts_at__lt=occupied_right,
            ends_at__gt=occupied_left,
        ).exists()
    ):
        raise BookingConflict("La ressource est déjà occupée sur ce créneau.")
    return left, right, occupied_left, occupied_right


def slots(offer, day):
    """Une journée, lectures groupées et au plus 500 propositions sans identité de tiers."""
    from .timeutils import local_datetime

    if not offer_ready(offer):
        return []
    tz = ZoneInfo(offer.calendar.timezone)
    left = datetime.combine(day, datetime.min.time(), tz).astimezone(UTC)
    right = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).astimezone(UTC)
    extra = timedelta(minutes=offer.duration_minutes + offer.before_minutes + offer.after_minutes)
    windows = list(
        BookingWindow.objects.filter(
            calendar=offer.calendar,
            resource=offer.resource,
            is_active=True,
            starts_at__lt=right + extra,
            ends_at__gt=left - extra,
        ).order_by("starts_at", "pk")[:101]
    )
    if len(windows) > 100:
        raise ValidationError(
            "Plus de 100 plages autour de cette journée : simplifiez les disponibilités."
        )
    closed = [(row.starts_at, row.ends_at) for row in windows if row.is_closed]
    occupied = list(
        Booking.objects.filter(
            resource=offer.resource,
            state__in=Booking.OCCUPYING,
            occupied_starts_at__lt=right + extra,
            occupied_ends_at__gt=left - extra,
        ).values_list("occupied_starts_at", "occupied_ends_at")[:1001]
    )
    events = list(
        Event.objects.filter(
            site=offer.site,
            resources=offer.resource,
            deleted_at__isnull=True,
            starts_at__lt=right + extra,
            ends_at__gt=left - extra,
        )
        .values_list("starts_at", "ends_at")
        .distinct()[:1001]
    )
    if len(occupied) > 1000 or len(events) > 1000:
        raise ValidationError("Trop d'occupations dans cette journée pour proposer des créneaux.")
    unavailable = closed + occupied + events
    now = timezone.now()
    earliest, latest = (
        now + timedelta(minutes=offer.lead_minutes),
        now + timedelta(days=offer.horizon_days),
    )
    result = set()
    checked = 0
    for window in windows:
        if window.is_closed:
            continue
        origin = window.starts_at.astimezone(UTC)
        step = timedelta(minutes=offer.step_minutes)
        offset = max(0, int((left - origin).total_seconds() // step.total_seconds()))
        current = origin + offset * step
        while current < min(right, window.ends_at.astimezone(UTC)):
            checked += 1
            if checked > 1500:
                raise ValidationError(
                    "Trop de créneaux à examiner : augmentez le pas ou simplifiez les plages."
                )
            if current >= left and earliest <= current <= latest:
                _, _, occupied_left, occupied_right = intervals(offer, current)
                covered = origin <= occupied_left and occupied_right <= window.ends_at
                collision = any(a < occupied_right and b > occupied_left for a, b in unavailable)
                try:
                    local_datetime(
                        current.astimezone(tz).replace(tzinfo=None).isoformat(),
                        offer.calendar.timezone,
                    )
                except ValidationError:
                    covered = False
                if covered and not collision:
                    result.add(current)
                    if len(result) > 500:
                        raise ValidationError("Plus de 500 créneaux : augmentez le pas de l'offre.")
            current += step
    return [value.astimezone(tz) for value in sorted(result)]


def authorize(actor, site, action):
    codes = ["planning.view_booking", f"planning.{action}"]
    if action == "add_booking":
        codes.append("products.view_product")
    if not all(has_site_permission(actor, site, code) for code in codes):
        raise ValidationError("Vous ne disposez plus des droits requis sur ce site.")


def record(booking, actor, action, reason="", previous=None):
    details = {
        "state": booking.state,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }
    if previous:
        details["previous"] = previous
    BookingHistory.objects.create(
        booking=booking, actor=actor, action=action, reason=reason, details=details
    )
    AuditEvent.objects.create(
        actor=actor,
        site_id=booking.site_id,
        action=f"planning.booking.{action}",
        entity_type="planning.booking",
        entity_id=str(booking.pk),
    )
    BookingNotification.objects.create(
        booking=booking, event_version=booking.version, recipient=booking.offer.recipient
    )


@transaction.atomic
def create(site_id, offer_id, start, details, submission_id, quote_token, actor):
    site = Site.objects.select_for_update(no_key=True).exclude(pk=1).get(pk=site_id, is_active=True)
    authorize(actor, site, "add_booking")
    request_hash = fingerprint(
        {
            "offer": offer_id,
            "start": start.astimezone(UTC).isoformat(),
            "details": details,
            "quote": quote_token,
        }
    )
    repeated = Booking.objects.filter(site=site, submission_id=submission_id).first()
    if repeated:
        if repeated.payload_hash != request_hash:
            raise BookingConflict("Cette soumission a déjà servi à une autre réservation.")
        return repeated
    offer = offer_queryset(site).get(pk=offer_id)
    Resource.objects.select_for_update().get(pk=offer.resource_id, site=site)
    if not offer_ready(offer):
        raise BookingConflict("Cette offre n'accepte plus de nouvelles réservations.")
    snapshot = check_quote(offer, quote_token)
    dates = validate_slot(offer, start)
    booking = Booking(
        site=site,
        offer=offer,
        resource=offer.resource,
        submission_id=submission_id,
        payload_hash=request_hash,
        snapshot=snapshot,
        starts_at=dates[0],
        ends_at=dates[1],
        occupied_starts_at=dates[2],
        occupied_ends_at=dates[3],
        **details,
    )
    booking.full_clean()
    booking.save()
    record(booking, actor, "created")
    return booking


@transaction.atomic
def transition(site_id, booking_id, action, version, reason, actor, start=None):
    site = Site.objects.select_for_update(no_key=True).exclude(pk=1).get(pk=site_id, is_active=True)
    permission = (
        "move_booking"
        if action == "move"
        else "cancel_booking"
        if action == "cancel"
        else "process_booking"
    )
    authorize(actor, site, permission)
    booking = Booking.objects.select_for_update().get(site=site, pk=booking_id)
    offer = offer_queryset(site).get(pk=booking.offer_id)
    booking.offer = offer
    Resource.objects.select_for_update().get(pk=booking.resource_id, site=site)
    if booking.version != version:
        raise BookingConflict("La réservation a changé. Rechargez sa fiche.")
    now = timezone.now()
    previous = {
        "state": booking.state,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }
    if action == "confirm" and booking.state == "pending":
        if not offer.calendar.is_active or not offer.resource.is_active:
            raise BookingConflict("Réactivez le planning et la ressource pour confirmer.")
        validate_slot(offer, booking.starts_at, exclude=booking.pk, snapshot=booking.snapshot)
        booking.state = "confirmed"
    elif action == "refuse" and booking.state == "pending":
        booking.state = "refused"
    elif action == "cancel" and booking.state in ("pending", "confirmed"):
        if now > booking.starts_at - timedelta(minutes=booking.snapshot["cancellation_minutes"]):
            raise BookingConflict(
                "Le préavis d'annulation conservé dans la réservation est dépassé."
            )
        booking.state = "cancelled"
    elif action in ("complete", "no_show") and booking.state == "confirmed":
        if now < booking.ends_at:
            raise BookingConflict("Attendez la fin du créneau pour clôturer ou déclarer l'absence.")
        booking.state = "completed" if action == "complete" else "no_show"
    elif action == "move" and booking.state in ("pending", "confirmed") and start is not None:
        if not offer.calendar.is_active or not offer.resource.is_active:
            raise BookingConflict("Réactivez le planning et la ressource pour déplacer.")
        dates = validate_slot(offer, start, exclude=booking.pk, snapshot=booking.snapshot)
        booking.starts_at, booking.ends_at, booking.occupied_starts_at, booking.occupied_ends_at = (
            dates
        )
    else:
        raise ValidationError("Cette transition n'est pas permise depuis l'état actuel.")
    booking.version += 1
    booking.full_clean()
    booking.save()
    record(booking, actor, action, reason, previous)
    return booking


def validate_event_occupation(event, resource_ids=None):
    """Les événements liés à une ressource bloquent ses réservations, même privés."""
    if event.deleted_at or not event.starts_at or not event.ends_at:
        return
    ids = (
        resource_ids
        if resource_ids is not None
        else list(event.resources.values_list("pk", flat=True))
        if event.pk
        else []
    )
    if (
        ids
        and Booking.objects.filter(
            site_id=event.site_id,
            resource_id__in=ids,
            state__in=Booking.OCCUPYING,
            occupied_starts_at__lt=event.ends_at,
            occupied_ends_at__gt=event.starts_at,
        ).exists()
    ):
        raise ValidationError(
            "Une réservation occupe une ressource de cet événement. "
            "Modifiez le créneau ou les ressources."
        )
