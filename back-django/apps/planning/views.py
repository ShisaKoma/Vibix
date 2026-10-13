from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.views import secure_page
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .forms import VIEWS, EventForm, LifecycleForm, ResourceForm, SchedulerForm
from .models import Event, Resource
from .recurrence import create_series, touch_series
from .scheduler import filter_events, navigation, presentation, timed_days


def context(request, site_id, action="view_event"):
    site = get_object_or_404(Site.objects.for_user(request.user).exclude(pk=1), pk=site_id)
    if not has_site_permission(
        request.user, site, "planning.view_event"
    ) or not has_site_permission(request.user, site, f"planning.{action}"):
        raise Http404
    return {
        "site": site,
        "planning_active": True,
        **{
            f"can_{verb}": has_site_permission(request.user, site, f"planning.{verb}_event")
            for verb in ("add", "change", "delete", "publish", "restore")
        },
        "can_manage_resources": has_site_permission(request.user, site, "planning.change_resource"),
        "can_add_resource": has_site_permission(request.user, site, "planning.add_resource"),
        "can_view_bookings": has_site_permission(request.user, site, "planning.view_booking"),
    }


def audit(request, entry, action):
    AuditEvent.objects.create(
        actor=request.user,
        site=entry.site,
        action=f"planning.{entry._meta.model_name}.{action}",
        entity_type=f"planning.{entry._meta.model_name}",
        entity_id=str(entry.pk),
    )


@secure_page
@login_required
@require_GET
def calendar(request, site_id):
    data = context(request, site_id)
    inputs = {
        "date": timezone.localdate().isoformat(),
        "view": "month",
        "timezone": "Europe/Paris",
        **request.GET.dict(),
    }
    form = SchedulerForm(inputs, site=data["site"])
    data.update(search_form=form, events=[], days=[], hours=[])
    if form.is_valid():
        values = form.cleaned_data
        items, start, end, tz = filter_events(
            Event.objects.filter(site=data["site"], deleted_at__isnull=True).prefetch_related(
                "resources"
            ),
            values,
        )
        events = list(items[:501])
        if len(events) > 500:
            form.add_error(
                None,
                "Plus de 500 événements : réduisez la période ou filtrez les ressources/types.",
            )
        else:
            days, hours = presentation(events, start, end, tz)
            if values["view"] == "month":
                anchor = values["date"].replace(day=1)
                previous = (anchor - timedelta(days=1)).replace(day=1)
                following = (anchor.replace(day=28) + timedelta(days=4)).replace(day=1)
            else:
                delta = timedelta(days=1 if values["view"] == "day" else 7)
                previous, following = values["date"] - delta, values["date"] + delta
            data.update(
                events=events,
                days=days,
                hours=hours,
                timed_days=timed_days(days, tz)
                if values["view"] in {"day", "week", "workweek"}
                else [],
                start=start,
                end=end - timedelta(days=1),
                display_timezone=values["timezone"],
                selected_view=values["view"],
                previous_url=navigation(values, previous),
                next_url=navigation(values, following),
                today_url=navigation(values, timezone.localdate(timezone=tz)),
                view_links=[
                    {
                        "label": label,
                        "value": value,
                        "url": navigation({**values, "view": value}, values["date"]),
                    }
                    for value, label in VIEWS
                ],
                timeline_rows=[
                    {
                        "resource": r,
                        "cells": [
                            {
                                "date": d["date"],
                                "events": [e for e in d["events"] if r in e.resources.all()],
                            }
                            for d in days
                        ],
                    }
                    for r in Resource.objects.filter(site=data["site"])
                ],
                unassigned=[e for e in events if not e.resources.all()],
            )
    return render(request, "planning/calendar.html", data)


@secure_page
@login_required
@require_GET
def detail(request, site_id, pk):
    data = context(request, site_id)
    data["event"] = get_object_or_404(
        Event.objects.prefetch_related("resources"), site=data["site"], pk=pk
    )
    return render(request, "planning/detail.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, site_id, pk=None):
    data = context(request, site_id, "change_event" if pk else "add_event")
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            # Serialise repeated submissions before checking the unique request key.
            Site.objects.select_for_update().get(pk=data["site"].pk)
        entry = (
            get_object_or_404(
                Event.objects.select_for_update(), site=data["site"], pk=pk, deleted_at__isnull=True
            )
            if pk
            else Event(site=data["site"])
        )
        if not pk and request.method == "GET":
            initial = SchedulerForm(
                {
                    "date": request.GET.get("date", timezone.localdate().isoformat()),
                    "view": "day",
                    "timezone": request.GET.get("timezone", "Europe/Paris"),
                },
                site=data["site"],
            )
            if initial.is_valid():
                try:
                    hour = int(request.GET.get("hour", "9"))
                    if not 0 <= hour <= 23:
                        raise ValueError
                    entry.timezone = initial.cleaned_data["timezone"]
                    entry.starts_at = datetime.combine(
                        initial.cleaned_data["date"], datetime.min.time(), ZoneInfo(entry.timezone)
                    ) + timedelta(hours=hour)
                    entry.ends_at = entry.starts_at + timedelta(hours=1)
                except ValueError:
                    pass
        form = EventForm(
            request.POST if request.method == "POST" else None,
            instance=entry,
            site=data["site"],
            can_publish=data["can_publish"],
        )
        if request.method == "POST" and form.is_valid():
            repeated = (
                Event.objects.filter(submission_id=form.cleaned_data["submission_id"]).first()
                if not pk
                else None
            )
            if repeated:
                equal = repeated.site_id == data["site"].pk and all(
                    getattr(repeated, key) == form.cleaned_data[key]
                    for key in EventForm.Meta.fields
                    if key != "resources" and key in form.cleaned_data
                )
                equal = equal and set(repeated.resources.values_list("pk", flat=True)) == set(
                    form.cleaned_data["resources"].values_list("pk", flat=True)
                )
                if equal:
                    rule = repeated.series
                    equal = (not rule and not form.cleaned_data.get("repeat_frequency")) or bool(
                        rule
                        and rule.frequency == form.cleaned_data.get("repeat_frequency")
                        and rule.interval == (form.cleaned_data.get("repeat_interval") or 1)
                        and rule.count == (form.cleaned_data.get("repeat_count") or 10)
                    )
                if equal:
                    return redirect("planning:detail", site_id=site_id, pk=repeated.pk)
                form.add_error(
                    None,
                    "Soumission déjà utilisée pour un autre événement. Rechargez le formulaire.",
                )
                status = 409
            elif pk and form.cleaned_data["version"] != entry.version:
                form.add_error(
                    None, "Cet événement a changé. Rechargez sa fiche avant d’enregistrer."
                )
                status = 409
            else:
                entry = form.save(commit=False)
                if not pk:
                    entry.submission_id = form.cleaned_data["submission_id"]
                else:
                    entry.version += 1
                if entry.series_id:
                    touch_series(entry)
                entry.full_clean()
                entry.save()
                form.save_m2m()
                if not entry.series_id and form.cleaned_data.get("repeat_frequency"):
                    if not data["can_add"]:
                        raise Http404
                    create_series(
                        entry,
                        form.cleaned_data,
                        list(form.cleaned_data["resources"]),
                        can_publish=data["can_publish"],
                        audit=lambda obj, action: audit(request, obj, action),
                    )
                audit(request, entry, "updated" if pk else "created")
                messages.success(request, "Événement enregistré.")
                return redirect("planning:detail", site_id=site_id, pk=entry.pk)
    data.update(form=form, event=entry if pk else None)
    return render(request, "planning/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def lifecycle(request, site_id, pk, restore=False):
    data = context(request, site_id, "restore_event" if restore else "delete_event")
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            Site.objects.select_for_update().get(pk=data["site"].pk)
        entry = get_object_or_404(
            Event.objects.select_for_update(),
            site=data["site"],
            pk=pk,
            deleted_at__isnull=not restore,
        )
        form = LifecycleForm(
            request.POST if request.method == "POST" else None, initial={"version": entry.version}
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != entry.version:
                form.add_error(None, "Cet événement a changé. Rechargez la fiche.")
                status = 409
            else:
                entry.deleted_at = None if restore else timezone.now()
                entry.is_online = False
                try:
                    entry.full_clean()
                except ValidationError as error:
                    form.add_error(None, error.messages)
                    status = 409
                else:
                    touch_series(entry)
                    entry.version += 1
                    entry.save()
                    audit(request, entry, "restored" if restore else "archived")
                    return redirect("planning:detail", site_id=site_id, pk=pk)
    data.update(event=entry, form=form, restore=restore)
    return render(request, "planning/lifecycle.html", data, status=status)


@secure_page
@login_required
@require_GET
def trash(request, site_id):
    data = context(request, site_id)
    data["events"] = Event.objects.filter(site=data["site"], deleted_at__isnull=False)
    return render(request, "planning/trash.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
def resources(request, site_id, pk=None):
    action = (
        "change_resource" if pk else ("add_resource" if request.method == "POST" else "view_event")
    )
    data = context(request, site_id, action)
    status = 200
    with transaction.atomic():
        if request.method == "POST":
            Site.objects.select_for_update().get(pk=data["site"].pk)
        entry = (
            get_object_or_404(Resource.objects.select_for_update(), pk=pk, site=data["site"])
            if pk
            else Resource(site=data["site"])
        )
        form = ResourceForm(request.POST if request.method == "POST" else None, instance=entry)
        if request.method == "POST" and form.is_valid():
            if pk and form.cleaned_data["version"] != entry.version:
                form.add_error(None, "La ressource a changé. Rechargez la fiche.")
                status = 409
            else:
                entry = form.save(commit=False)
                if pk:
                    entry.version += 1
                entry.full_clean()
                entry.save()
                audit(request, entry, "updated" if pk else "created")
                return redirect("planning:resources", site_id=site_id)
    data.update(resources=Resource.objects.filter(site=data["site"]), form=form, editing=bool(pk))
    return render(request, "planning/resources.html", data, status=status)
