import re
from datetime import timedelta

from django import forms
from django.http import JsonResponse
from django.utils import timezone

from apps.core.api import api_get, paginated_data, public_site

from .models import Event, Resource


class PeriodForm(forms.Form):
    start = forms.DateTimeField()
    end = forms.DateTimeField()
    kind = forms.ChoiceField(required=False, choices=[("", "Tous"), *Event.Kind.choices])
    resource_id = forms.IntegerField(required=False, min_value=1, max_value=2**63 - 1)

    def clean(self):
        for field in ("start", "end"):
            raw = self.data.get(field, "")
            if not re.search(r"(Z|[+-]\d{2}:\d{2})$", raw):
                self.add_error(field, "Un décalage UTC explicite est requis.")
        data = super().clean()
        if data.get("start") and data.get("end"):
            if not timedelta(0) < data["end"] - data["start"] <= timedelta(days=93):
                raise forms.ValidationError("La période doit être positive et limitée à 93 jours.")
        return data


def serialize(event):
    return {
        "id": event.pk,
        "title": event.title,
        "kind": event.kind,
        "description": event.description,
        "location": event.location,
        "link": event.link,
        "starts_at": event.starts_at.isoformat(),
        "ends_at": event.ends_at.isoformat(),
        "timezone": event.timezone,
        "all_day": event.all_day,
        "resources": [
            {"id": r.pk, "name": r.name}
            for r in event.resources.all()
            if r.site_id == event.site_id
        ],
    }


@api_get
def events(request, site_id, pk=None):
    site = public_site(site_id)
    if site is None or not site.module_enabled("planning"):
        return JsonResponse({"error": "not_found"}, status=404)
    items = Event.objects.filter(
        site=site, is_online=True, deleted_at__isnull=True
    ).prefetch_related("resources")
    if pk is not None:
        event = items.filter(pk=pk).first()
        return (
            JsonResponse(serialize(event))
            if event
            else JsonResponse({"error": "not_found"}, status=404)
        )
    now = timezone.now()
    inputs = {
        "start": now.isoformat(),
        "end": (now + timedelta(days=31)).isoformat(),
        **request.GET.dict(),
    }
    form = PeriodForm(inputs)
    if not form.is_valid():
        return JsonResponse({"error": "invalid_filters", "fields": list(form.errors)}, status=400)
    values = form.cleaned_data
    items = items.filter(starts_at__lt=values["end"], ends_at__gt=values["start"])
    if values["kind"]:
        items = items.filter(kind=values["kind"])
    if values["resource_id"]:
        resource = Resource.objects.filter(site=site, pk=values["resource_id"]).first()
        items = items.filter(resources=resource) if resource else items.none()
    return paginated_data(request, items, serialize)
