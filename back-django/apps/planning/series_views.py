from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.cobweb.views import secure_page
from apps.tenancy.models import Site

from .forms import EventForm, MoveForm
from .models import Event, EventSeries, Resource
from .recurrence import local_instant, prototype, replace_series, touch_series
from .views import audit, context


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def edit_series(request, site_id, pk):
    data = context(request, site_id, "change_event")
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            Site.objects.select_for_update().get(pk=site_id)
        series = get_object_or_404(
            EventSeries.objects.select_for_update(), site=data["site"], pk=pk
        )
        entry = prototype(series)
        form = EventForm(
            request.POST if request.method == "POST" else None,
            instance=entry,
            site=data["site"],
            can_publish=data["can_publish"],
            editing_series=True,
        )
        form.fields["repeat_frequency"].required = True
        form.fields["resources"].queryset = Resource.objects.filter(site=data["site"]).filter(
            Q(is_active=True) | Q(pk__in=series.template["resource_ids"])
        )
        form.initial.update(
            version=series.version,
            submission_id=uuid4(),
            repeat_frequency=series.frequency,
            repeat_interval=series.interval,
            repeat_count=series.count,
            resources=series.template["resource_ids"],
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != series.version:
                form.add_error(None, "La série ou une occurrence a changé. Rechargez la série.")
                status = 409
            else:
                try:
                    # Roll back the entire series if validation fails after some writes.
                    with transaction.atomic():
                        replace_series(
                            series,
                            form.instance,
                            form.cleaned_data,
                            list(form.cleaned_data["resources"]),
                            can_add=data["can_add"],
                            can_publish=data["can_publish"],
                            audit=lambda obj, action: audit(request, obj, action),
                        )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    messages.success(request, "Série enregistrée. Les exceptions sont conservées.")
                    return redirect("planning:calendar", site_id=site_id)
    data.update(
        form=form, series=series, exception_count=series.events.filter(is_exception=True).count()
    )
    return render(request, "planning/form.html", data, status=status)


def moved_dates(event, start):
    if not event.all_day:
        duration = event.ends_at.astimezone(UTC) - event.starts_at.astimezone(UTC)
        return start, start.astimezone(UTC) + duration
    tz = ZoneInfo(event.timezone)
    local = start.astimezone(tz)
    if local.time() != datetime.min.time():
        raise ValidationError(
            "Une journée entière commence à minuit dans le fuseau de l’événement."
        )
    days = (event.ends_at.astimezone(tz).date() - event.starts_at.astimezone(tz).date()).days
    return start, local_instant(local.replace(tzinfo=None) + timedelta(days=days), event.timezone)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def move(request, site_id, pk):
    data = context(request, site_id, "change_event")
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            Site.objects.select_for_update().get(pk=site_id)
        entry = get_object_or_404(
            Event.objects.select_for_update(), site=data["site"], pk=pk, deleted_at__isnull=True
        )
        display_zone = request.GET.get("timezone", entry.timezone)
        try:
            tz = ZoneInfo(entry.timezone if entry.all_day else display_zone)
        except (ValueError, KeyError):
            tz = ZoneInfo(entry.timezone)
        current = entry.starts_at.astimezone(tz)
        initial = {
            "date": current.date().isoformat(),
            "hour": current.strftime("%H:%M"),
            "timezone": str(tz),
            "version": entry.version,
        }
        initial["date"] = request.GET.get("date", initial["date"])
        if "hour" in request.GET and not entry.all_day:
            try:
                hour = int(request.GET["hour"])
                if 0 <= hour <= 23:
                    initial["hour"] = f"{hour:02d}:{current.minute:02d}"
            except ValueError:
                pass
        if "version" in request.GET:
            initial["version"] = request.GET["version"]
        form = MoveForm(request.POST if request.method == "POST" else None, initial=initial)
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != entry.version:
                form.add_error(None, "Cet événement a changé. Rechargez-le avant de le déplacer.")
                status = 409
            else:
                previous_dates = entry.starts_at, entry.ends_at
                try:
                    start, end = moved_dates(entry, form.cleaned_data["new_start"])
                    entry.starts_at, entry.ends_at = start, end
                    entry.full_clean()
                except ValidationError as error:
                    entry.starts_at, entry.ends_at = previous_dates
                    form.add_error(None, error)
                else:
                    touch_series(entry)
                    entry.version += 1
                    entry.save()
                    audit(request, entry, "moved")
                    messages.success(
                        request, "Occurrence déplacée." if entry.series_id else "Événement déplacé."
                    )
                    return redirect("planning:detail", site_id=site_id, pk=pk)
    data.update(event=entry, form=form)
    return render(request, "planning/move.html", data, status=status)
