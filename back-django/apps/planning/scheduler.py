"""Period selection and presentation, without loading the entire event catalogue."""

from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.db.models import Q


def period(anchor, view):
    if view == "day":
        return anchor, anchor + timedelta(days=1)
    if view == "month":
        first = anchor.replace(day=1)
        following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        start = first - timedelta(days=first.weekday())
        end = following + timedelta(days=(-following.weekday()) % 7)
        return start, end
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=5 if view == "workweek" else 7)


def filter_events(queryset, data):
    start, end = period(data["date"], data["view"])
    tz = ZoneInfo(data["timezone"])
    queryset = queryset.filter(
        starts_at__lt=datetime.combine(end, time.min, tz),
        ends_at__gt=datetime.combine(start, time.min, tz),
    )
    if data.get("kind"):
        queryset = queryset.filter(kind=data["kind"])
    if data.get("resource"):
        queryset = queryset.filter(resources=data["resource"])
    if data.get("q"):
        queryset = queryset.filter(Q(title__icontains=data["q"]) | Q(location__icontains=data["q"]))
    return queryset, start, end, tz


def presentation(events, start, end, tz):
    days = []
    day = start
    while day < end:
        left = datetime.combine(day, time.min, tz)
        right = datetime.combine(day + timedelta(days=1), time.min, tz)
        matching = [e for e in events if e.starts_at < right and e.ends_at > left]
        days.append({"date": day, "events": matching})
        day += timedelta(days=1)
    hours = [{"label": f"{hour:02d}:00", "cells": []} for hour in range(24)]
    for day in days:
        for hour, row in enumerate(hours):
            row["cells"].append(
                {
                    "date": day["date"],
                    "hour": hour,
                    "events": [
                        e
                        for e in day["events"]
                        if not e.all_day
                        and (
                            e.starts_at.astimezone(tz).date() == day["date"]
                            and e.starts_at.astimezone(tz).hour == hour
                            or e.starts_at.astimezone(tz).date() < day["date"]
                            and hour == 0
                        )
                    ],
                }
            )
    return days, hours


def timed_days(days, tz):
    """Clip to local days, then place intervals in lanes on an elapsed-minute axis.

    UTC arithmetic keeps repeated/missing hours and cross-midnight events truthful.
    Six-minute visual markers are included in collision detection for very short events.
    """
    result = []
    for day in days:
        left = datetime.combine(day["date"], time.min, tz).astimezone(UTC)
        right = datetime.combine(day["date"] + timedelta(days=1), time.min, tz).astimezone(UTC)
        minutes = (right - left).total_seconds() / 60
        blocks = []
        for event in day["events"]:
            if event.all_day:
                continue
            start = event.starts_at.astimezone(UTC)
            end = event.ends_at.astimezone(UTC)
            offset = (max(start, left) - left).total_seconds() / 60
            duration = (min(end, right) - max(start, left)).total_seconds() / 60
            local_start, local_end = start.astimezone(tz), end.astimezone(tz)
            time_format = "%d/%m %H:%M"
            if minutes != 1440 or local_start.utcoffset() != local_end.utcoffset():
                time_format += " (UTC%z)"
            blocks.append(
                {
                    "event": event,
                    "offset": offset,
                    "duration": duration,
                    "time_label": f"{local_start.strftime(time_format)} – "
                    f"{local_end.strftime(time_format)}",
                    "visual_end": min(minutes, offset + max(6, duration)),
                    "continues_before": start < left,
                    "continues_after": end > right,
                }
            )
        blocks.sort(key=lambda block: (block["offset"], -block["duration"], block["event"].pk))
        group, ends = [], []
        for block in blocks:
            if ends and max(ends) <= block["offset"]:
                for member in group:
                    member["lanes"] = len(ends)
                group, ends = [], []
            lane = next((i for i, end in enumerate(ends) if end <= block["offset"]), len(ends))
            if lane == len(ends):
                ends.append(block["visual_end"])
            else:
                ends[lane] = block["visual_end"]
            block["lane"] = lane
            group.append(block)
        for member in group:
            member["lanes"] = len(ends)
        slots = []
        instant = left
        while instant < right:
            local = instant.astimezone(tz)
            slots.append(
                {
                    "hour": local.hour,
                    "label": local.strftime("%H:%M"),
                    "offset": (instant - left).total_seconds() / 60,
                    "duration": min(60, (right - instant).total_seconds() / 60),
                    "utc_offset": local.strftime("%z"),
                }
            )
            instant += timedelta(hours=1)
        result.append(
            {
                **day,
                "all_day_events": [event for event in day["events"] if event.all_day],
                "minutes": minutes,
                "blocks": blocks,
                "slots": slots,
                "max_lanes": max((block["lanes"] for block in blocks), default=1),
            }
        )
    return result


def navigation(data, anchor):
    values = {
        key: str(value.pk) if key == "resource" and value else str(value)
        for key, value in data.items()
        if value not in (None, "")
    }
    values["date"] = anchor.isoformat()
    return "?" + urlencode(values)
