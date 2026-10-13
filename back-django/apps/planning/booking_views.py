from datetime import datetime
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.views import secure_page
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from . import bookings
from .booking_forms import (
    ActionForm,
    AvailabilityForm,
    CalendarForm,
    CreateForm,
    FilterForm,
    OfferForm,
    WindowForm,
)
from .booking_models import (
    Booking,
    BookingCalendar,
    BookingNotification,
    BookingOffer,
    BookingWindow,
)

CONFIG = {
    "calendars": (BookingCalendar, CalendarForm, "Plannings de réservation"),
    "offers": (BookingOffer, OfferForm, "Offres de réservation"),
    "windows": (BookingWindow, WindowForm, "Plages et fermetures"),
}
ACTIONS = {
    "confirm": ("process_booking", "Confirmer et occuper la ressource"),
    "refuse": ("process_booking", "Refuser la demande"),
    "cancel": ("cancel_booking", "Annuler et libérer la ressource"),
    "move": ("move_booking", "Déplacer la réservation"),
    "complete": ("process_booking", "Marquer terminée"),
    "no_show": ("process_booking", "Déclarer l'absence"),
}


def context(request, site_id, permission="view_booking"):
    site = get_object_or_404(Site.objects.for_user(request.user).exclude(pk=1), pk=site_id)
    if request.method == "POST":
        site = get_object_or_404(
            Site.objects.select_for_update(no_key=True), pk=site.pk, is_active=True
        )
    if not all(
        has_site_permission(request.user, site, f"planning.{key}")
        for key in ("view_booking", permission)
    ):
        raise Http404
    permissions = {
        "can_add_booking": "planning.add_booking",
        "can_personal": "planning.view_booking_personal_data",
        "can_process": "planning.process_booking",
        "can_move": "planning.move_booking",
        "can_cancel": "planning.cancel_booking",
        "can_retry": "planning.retry_booking_notification",
    }
    data = {
        "site": site,
        "planning_active": True,
        **{
            key: has_site_permission(request.user, site, value)
            for key, value in permissions.items()
        },
    }
    data["can_add_booking"] = data["can_add_booking"] and has_site_permission(
        request.user, site, "products.view_product"
    )
    data["configuration_links"] = [
        {"kind": kind, "title": spec[2]}
        for kind, spec in CONFIG.items()
        if has_site_permission(request.user, site, f"planning.view_{spec[0]._meta.model_name}")
    ]
    return data


def configuration_items(model, site):
    if model is BookingWindow:
        return model.objects.filter(calendar__site=site).select_related("calendar", "resource")
    items = model.objects.filter(site=site)
    return (
        items.select_related("calendar", "resource", "variant__product", "tariff")
        if model is BookingOffer
        else items
    )


def validate_future_windows(calendar):
    items = list(
        Booking.objects.filter(
            offer__calendar=calendar, state__in=Booking.OCCUPYING, ends_at__gt=timezone.now()
        )
        .select_related("resource")
        .order_by("pk")[:1001]
    )
    if len(items) > 1000:
        raise ValidationError(
            "Plus de 1000 engagements futurs : faites valider la modification des disponibilités."
        )
    for booking in items:
        windows = BookingWindow.objects.filter(
            calendar=calendar, resource=booking.resource, is_active=True
        )
        if (
            not windows.filter(
                is_closed=False,
                starts_at__lte=booking.occupied_starts_at,
                ends_at__gte=booking.occupied_ends_at,
            ).exists()
            or windows.filter(
                is_closed=True,
                starts_at__lt=booking.occupied_ends_at,
                ends_at__gt=booking.occupied_starts_at,
            ).exists()
        ):
            raise ValidationError(
                "Cette modification rendrait indisponible un engagement confirmé. "
                "Déplacez ou traitez d'abord la réservation."
            )


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def configuration(request, site_id, kind, pk=None):
    if kind not in CONFIG:
        raise Http404
    model, form_class, title = CONFIG[kind]
    verb = "change" if pk else "add" if request.method == "POST" else "view"
    data = context(request, site_id, f"{verb}_{model._meta.model_name}")
    items = configuration_items(model, data["site"])
    instance = get_object_or_404(items, pk=pk) if pk else model()
    if model is not BookingWindow:
        instance.site = data["site"]
    can_add = has_site_permission(
        request.user, data["site"], f"planning.add_{model._meta.model_name}"
    )
    if model is BookingOffer:
        can_configure = all(
            has_site_permission(request.user, data["site"], code)
            for code in ("products.view_product", "products.configure_reservations")
        )
        can_add = can_add and can_configure
        if request.method == "POST" or pk:
            if not can_configure:
                raise Http404
    form = (
        form_class(
            request.POST if request.method == "POST" else None, site=data["site"], instance=instance
        )
        if pk or can_add
        else None
    )
    status = 200
    if request.method == "POST" and form and form.is_valid():
        if pk and form.cleaned_data["version"] != instance.version:
            form.add_error(None, "Cette configuration a changé. Rechargez-la.")
            status = 409
        else:
            try:
                with transaction.atomic():
                    previous_calendar = (
                        BookingWindow.objects.get(pk=pk).calendar
                        if pk and model is BookingWindow
                        else None
                    )
                    entry = form.save(commit=False)
                    entry.full_clean()
                    if pk:
                        entry.version += 1
                    entry.save()
                    if model is BookingWindow:
                        validate_future_windows(entry.calendar)
                        if previous_calendar and previous_calendar.pk != entry.calendar_id:
                            validate_future_windows(previous_calendar)
                    AuditEvent.objects.create(
                        actor=request.user,
                        site=data["site"],
                        action=f"planning.{model._meta.model_name}.save",
                        entity_type=f"planning.{model._meta.model_name}",
                        entity_id=str(entry.pk),
                    )
            except ValidationError as error:
                form.add_error(None, error.messages)
                status = 409
            except IntegrityError:
                form.add_error(
                    None, "La configuration n'a pas été enregistrée. Rechargez avant de réessayer."
                )
                status = 409
            else:
                messages.success(request, "Configuration enregistrée.")
                return redirect("planning:booking-config", site_id=site_id, kind=kind)
    rows = [
        {
            "entry": item,
            "can_change": has_site_permission(
                request.user, data["site"], f"planning.change_{model._meta.model_name}"
            ),
        }
        for item in Paginator(items, 30).get_page(request.GET.get("page"))
    ]
    data.update(
        kind=kind,
        heading=title,
        form=form,
        rows=rows,
        page=Paginator(items, 30).get_page(request.GET.get("page")),
    )
    return render(request, "planning/bookings/configuration.html", data, status=status)


@secure_page
@login_required
@require_GET
def availability(request, site_id):
    data = context(request, site_id)
    if not has_site_permission(request.user, data["site"], "products.view_product"):
        raise Http404
    form = AvailabilityForm(request.GET or None, site=data["site"])
    data.update(form=form, slots=[])
    if form.is_valid():
        offer = bookings.offer_queryset(data["site"]).get(pk=form.cleaned_data["offer"].pk)
        try:
            values = bookings.slots(offer, form.cleaned_data["date"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            quote, _ = bookings.issue_quote(offer)
            data.update(
                offer=offer,
                quote=quote,
                slots=[
                    {
                        "value": value,
                        "query": urlencode({"offer": offer.pk, "starts_at": value.isoformat()}),
                    }
                    for value in values
                ],
                display_timezone=offer.calendar.timezone,
            )
    return render(request, "planning/bookings/availability.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def create(request, site_id):
    data = context(request, site_id, "add_booking")
    if not has_site_permission(request.user, data["site"], "products.view_product"):
        raise Http404
    raw_id = request.GET.get("offer", "")
    if not raw_id.isascii() or not raw_id.isdigit() or len(raw_id) > 18:
        raise Http404
    offer = get_object_or_404(bookings.offer_queryset(data["site"]), pk=int(raw_id))
    quote, token = bookings.issue_quote(offer)
    initial = {"submission_id": uuid4(), "quote_token": token}
    try:
        start = datetime.fromisoformat(request.GET.get("starts_at", ""))
        if timezone.is_aware(start):
            initial["starts_at"] = start.astimezone(ZoneInfo(offer.calendar.timezone)).strftime(
                "%Y-%m-%dT%H:%M"
            )
    except ValueError:
        pass
    form = CreateForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        zone=offer.calendar.timezone,
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            booking = bookings.create(
                site_id,
                offer.pk,
                values["starts_at"],
                {key: values[key] for key in ("name", "email", "phone")},
                values["submission_id"],
                values["quote_token"],
                request.user,
            )
        except bookings.QuoteChanged as error:
            form.add_error(None, error.messages)
            form.data = form.data.copy()
            form.data["quote_token"] = token
            form.data["confirm"] = ""
            status = 409
        except bookings.BookingConflict as error:
            form.add_error(None, error.messages)
            status = 409
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(
                request,
                "Réservation enregistrée en attente. La ressource sera occupée "
                "seulement après confirmation.",
            )
            return redirect("planning:booking-detail", site_id=site_id, pk=booking.pk)
    data.update(
        form=form,
        offer=offer,
        quote=quote,
        unavailable=not bookings.offer_ready(offer),
        heading="Créer une réservation interne",
    )
    return render(request, "planning/bookings/form.html", data, status=status)


@secure_page
@login_required
@require_GET
def listing(request, site_id):
    data = context(request, site_id)
    form = FilterForm(request.GET, site=data["site"])
    items = Booking.objects.filter(site=data["site"]).select_related("resource", "offer__calendar")
    if form.is_valid():
        values = form.cleaned_data
        for field, lookup in (
            ("state", "state"),
            ("resource", "resource"),
            ("calendar", "offer__calendar"),
        ):
            if values[field]:
                items = items.filter(**{lookup: values[field]})
        if values["date_from"]:
            items = items.filter(starts_at__date__gte=values["date_from"])
        if values["date_to"]:
            items = items.filter(starts_at__date__lte=values["date_to"])
    else:
        items = items.none()
    data.update(
        form=form,
        page=Paginator(items, 30).get_page(request.GET.get("page")),
        query=urlencode({key: value for key, value in request.GET.items() if key != "page"}),
    )
    return render(request, "planning/bookings/list.html", data)


@secure_page
@login_required
@require_GET
def detail(request, site_id, pk):
    data = context(request, site_id)
    booking = get_object_or_404(
        Booking.objects.select_related("resource", "offer"), site=data["site"], pk=pk
    )
    quote = booking.snapshot
    tz = ZoneInfo(quote["timezone"])
    allowed = (
        ["confirm", "refuse", "cancel", "move"]
        if booking.state == "pending"
        else ["cancel", "move", "complete", "no_show"]
        if booking.state == "confirmed"
        else []
    )
    actions = [
        {"key": key, "label": ACTIONS[key][1]}
        for key in allowed
        if has_site_permission(request.user, data["site"], "planning." + ACTIONS[key][0])
    ]
    data.update(
        booking=booking,
        quote=quote,
        display_start=booking.starts_at.astimezone(tz),
        display_end=booking.ends_at.astimezone(tz),
        actions=actions,
        history=booking.history.select_related("actor").order_by("-created_at", "-pk")[:100],
        notifications=booking.notifications.order_by("-event_version")[:100],
    )
    return render(request, "planning/bookings/detail.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def action(request, site_id, pk, action):
    if action not in ACTIONS:
        raise Http404
    permission, label = ACTIONS[action]
    data = context(request, site_id, permission)
    booking = get_object_or_404(Booking, site=data["site"], pk=pk)
    zone = booking.snapshot["timezone"]
    form = ActionForm(
        request.POST if request.method == "POST" else None,
        initial={
            "version": booking.version,
            "starts_at": booking.starts_at.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M"),
        },
        zone=zone,
        moving=action == "move",
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            bookings.transition(
                site_id,
                pk,
                action,
                form.cleaned_data["version"],
                form.cleaned_data["reason"],
                request.user,
                start=form.cleaned_data.get("starts_at"),
            )
        except bookings.BookingConflict as error:
            form.add_error(None, error.messages)
            status = 409
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            return redirect("planning:booking-detail", site_id=site_id, pk=pk)
    data.update(form=form, booking=booking, quote=booking.snapshot, heading=label)
    return render(request, "planning/bookings/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def retry_notification(request, site_id, pk, notification_id):
    data = context(request, site_id, "retry_booking_notification")
    booking = get_object_or_404(Booking, site=data["site"], pk=pk)
    job = get_object_or_404(
        BookingNotification.objects.select_for_update(), booking=booking, pk=notification_id
    )
    form = ActionForm(
        request.POST if request.method == "POST" else None,
        initial={"version": booking.version},
        zone=booking.snapshot["timezone"],
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["version"] != booking.version or job.state != "failed":
            form.add_error(None, "La demande a changé ou la notification n'est plus en échec.")
            status = 409
        else:
            job.state, job.attempts, job.next_attempt_at, job.lease = (
                "pending",
                0,
                timezone.now(),
                None,
            )
            job.save(update_fields=["state", "attempts", "next_attempt_at", "lease"])
            AuditEvent.objects.create(
                actor=request.user,
                site=data["site"],
                action="planning.bookingnotification.retry",
                entity_type="planning.bookingnotification",
                entity_id=str(job.pk),
            )
            return redirect("planning:booking-detail", site_id=site_id, pk=pk)
    data.update(form=form, booking=booking, heading="Relancer la notification interne")
    return render(request, "planning/bookings/form.html", data, status=status)
