"""Finite recurrence materialised as ordinary events, preserving wall-clock hours."""

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Event, EventSeries

TEMPLATE_FIELDS = (
    "title",
    "kind",
    "description",
    "location",
    "link",
    "starts_at",
    "ends_at",
    "timezone",
    "all_day",
    "is_online",
)


def local_instant(value, zone):
    tz = ZoneInfo(zone)
    value = value.replace(tzinfo=None)
    first = value.replace(tzinfo=tz, fold=0)
    second = value.replace(tzinfo=tz, fold=1)
    if (
        first.utcoffset() != second.utcoffset()
        or first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != value
    ):
        raise ValidationError(
            "Heure locale ambiguë ou inexistante dans une occurrence. Modifiez l’heure ou la règle."
        )
    return first


def snapshot(event, resources):
    data = {key: getattr(event, key) for key in TEMPLATE_FIELDS}
    for key in ("starts_at", "ends_at"):
        data[key] = data[key].isoformat()
    data["resource_ids"] = [r.pk for r in resources]
    return data


def prototype(series):
    values = {key: series.template[key] for key in TEMPLATE_FIELDS}
    for key in ("starts_at", "ends_at"):
        values[key] = datetime.fromisoformat(values[key])
    return Event(site_id=series.site_id, **values)


def occurrences(event, frequency, interval, count):
    tz = ZoneInfo(event.timezone)
    start = event.starts_at.astimezone(tz).replace(tzinfo=None)
    end = event.ends_at.astimezone(tz).replace(tzinfo=None)
    result = []
    period = 0
    while len(result) < count:
        if frequency == EventSeries.Frequency.MONTHLY:
            month = start.year * 12 + start.month - 1 + period * interval
            year, month = divmod(month, 12)
            month += 1
            if not 1900 <= year <= 2100 or year > start.year + 5:
                raise ValidationError("Limitez la série à cinq ans et aux années 1900 à 2100.")
            period += 1
            if start.day > monthrange(year, month)[1]:
                continue  # A monthly event on the 31st skips months without a 31st.
            candidate = start.replace(year=year, month=month)
            delta = candidate - start
        else:
            delta = timedelta(days=period * interval * (7 if frequency == "weekly" else 1))
            candidate = start + delta
            period += 1
        if not 1900 <= candidate.year <= 2100 or candidate - start > timedelta(days=366 * 5):
            raise ValidationError("Limitez la série à cinq ans et aux années 1900 à 2100.")
        left = local_instant(candidate, event.timezone)
        right = local_instant(end + delta, event.timezone)
        if right.astimezone(UTC) <= left.astimezone(UTC):
            raise ValidationError("Une occurrence a une durée invalide.")
        result.append((left, right))
    return result


def validate_rule(event, data):
    frequency = data.get("repeat_frequency")
    if not frequency:
        return []
    interval = data.get("repeat_interval") or 1
    count = data.get("repeat_count") or 10
    if (
        frequency not in EventSeries.Frequency.values
        or not 1 <= interval <= 12
        or not 2 <= count <= 100
    ):
        raise ValidationError(
            "Choisissez une règle valide, de 2 à 100 occurrences, avec un pas de 1 à 12."
        )
    return occurrences(event, frequency, interval, count)


def touch_series(event):
    """Caller owns the site lock; every exception invalidates bulk forms."""
    if event.series_id:
        series = event.series
        series.version += 1
        series.save(update_fields=["version"])
        event.is_exception = True


def create_series(event, data, resources, *, can_publish, audit):
    dates = validate_rule(event, data)
    if not dates:
        return
    series = EventSeries(
        site=event.site,
        frequency=data["repeat_frequency"],
        interval=data.get("repeat_interval") or 1,
        count=data.get("repeat_count") or 10,
        template=snapshot(event, resources),
    )
    series.full_clean()
    series.save()
    event.series = series
    event.occurrence_index = 0
    event.save(update_fields=["series", "occurrence_index"])
    for index, (start, end) in enumerate(dates[1:], 1):
        clone = prototype(series)
        clone.series = series
        clone.occurrence_index = index
        clone.starts_at, clone.ends_at = start, end
        if not can_publish:
            clone.is_online = False
        clone.booking_resource_ids = [r.pk for r in resources]
        clone.full_clean()
        clone.save()
        clone.resources.set(resources)
        audit(clone, "created")
    audit(series, "created")


def replace_series(series, event, data, resources, *, can_add, can_publish, audit):
    """Caller owns the series row lock; exceptions and their archive state are untouched."""
    dates = validate_rule(event, data)
    rows = {e.occurrence_index: e for e in series.events.select_for_update().order_by("pk")}
    if any(index not in rows for index in range(len(dates))) and not can_add:
        raise ValidationError(
            "Le droit de créer des événements est requis pour ajouter des occurrences."
        )
    if any(index not in rows for index in range(len(dates))) and any(
        not r.is_active for r in resources
    ):
        raise ValidationError("Réactivez les ressources avant d’ajouter de nouvelles occurrences.")
    for index, (start, end) in enumerate(dates):
        entry = rows.get(index)
        if entry and entry.is_exception:
            continue
        creating = entry is None
        if creating:
            entry = Event(site=series.site, series=series, occurrence_index=index)
        for key in TEMPLATE_FIELDS:
            if key not in ("starts_at", "ends_at", "is_online"):
                setattr(entry, key, getattr(event, key))
        entry.starts_at, entry.ends_at = start, end
        if can_publish:
            entry.is_online = event.is_online
        elif creating or entry.deleted_at:
            entry.is_online = False
        entry.deleted_at = None
        if not creating:
            entry.version += 1
        entry.booking_resource_ids = [r.pk for r in resources]
        entry.full_clean()
        entry.save()
        entry.resources.set(resources)
        audit(entry, "created" if creating else "updated")
    for index, entry in rows.items():
        if index >= len(dates) and not entry.is_exception and entry.deleted_at is None:
            entry.deleted_at = timezone.now()
            entry.is_online = False
            entry.version += 1
            entry.save()
            audit(entry, "archived")
    series.frequency = data["repeat_frequency"]
    series.interval = data.get("repeat_interval") or 1
    series.count = data.get("repeat_count") or 10
    series.template = snapshot(event, resources)
    series.version += 1
    series.full_clean()
    series.save()
    audit(series, "updated")
