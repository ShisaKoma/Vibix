from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditEvent

from .models import Event, EventSeries
from .tests import PlanningTests


class RecurrenceTests(TestCase):
    url = PlanningTests.url
    payload = PlanningTests.payload
    filters = PlanningTests.filters
    setUp = PlanningTests.setUp

    @classmethod
    def setUpTestData(cls):
        PlanningTests.setUpTestData.__func__(cls)

    def create(self, **kwargs):
        response = self.client.post(
            self.url("create"), self.payload(repeat_frequency="weekly", repeat_count=4, **kwargs)
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        return EventSeries.objects.get()

    def series_url(self, series):
        return reverse("planning:series-edit", args=[self.site.pk, series.pk])

    def series_payload(self, series, **kwargs):
        return self.payload(
            version=series.version,
            repeat_frequency=series.frequency,
            repeat_interval=series.interval,
            repeat_count=series.count,
            **kwargs,
        )

    def test_finite_series_is_published_as_regular_events_and_repeated_submit_is_idempotent(self):
        values = self.payload(repeat_frequency="weekly", repeat_count=4)
        for _ in range(2):
            self.assertEqual(self.client.post(self.url("create"), values).status_code, 302)
        series = EventSeries.objects.get()
        self.assertEqual(series.events.count(), 4)
        self.assertEqual(
            list(
                series.events.order_by("occurrence_index").values_list(
                    "occurrence_index", flat=True
                )
            ),
            [0, 1, 2, 3],
        )
        response = self.client.get(self.url("api-events"), self.filters(kind="webinar"))
        self.assertEqual(response.json()["count"], 4)
        self.assertNotIn("series", response.json()["results"][0])
        self.assertEqual(AuditEvent.objects.filter(action="planning.event.created").count(), 4)
        changed = {**values, "repeat_count": 5}
        self.assertEqual(self.client.post(self.url("create"), changed).status_code, 409)

    def test_weekly_keeps_local_hour_across_summer_time(self):
        series = self.create(starts_at="2026-03-21T10:00", ends_at="2026-03-21T11:00")
        events = list(series.events.order_by("occurrence_index"))
        self.assertEqual(
            [e.starts_at.astimezone(ZoneInfo("Europe/Paris")).hour for e in events], [10] * 4
        )
        self.assertEqual([e.starts_at.hour for e in events], [9, 9, 8, 8])

    def test_series_keeps_inactive_resources_but_cannot_expand_with_them(self):
        series = self.create()
        resource = self.resource
        resource.is_active = False
        resource.save()
        response = self.client.post(self.series_url(series), self.series_payload(series))
        self.assertEqual(response.status_code, 302)
        series.refresh_from_db()
        response = self.client.post(
            self.series_url(series),
            {**self.series_payload(series), "repeat_count": 5},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Réactivez les ressources")
        self.assertEqual(series.events.count(), 4)

    def test_monthly_skips_missing_month_days(self):
        values = self.payload(
            repeat_frequency="monthly",
            repeat_count=3,
            starts_at="2026-01-31T10:00",
            ends_at="2026-01-31T11:00",
        )
        self.assertEqual(self.client.post(self.url("create"), values).status_code, 302)
        self.assertEqual(
            [
                e.starts_at.month
                for e in EventSeries.objects.get().events.order_by("occurrence_index")
            ],
            [1, 3, 5],
        )

    def test_daily_step_all_day_and_dst_duration(self):
        values = self.payload(
            repeat_frequency="daily",
            repeat_count=3,
            repeat_interval=1,
            starts_at="2026-03-28T00:00",
            ends_at="2026-03-29T00:00",
            all_day="on",
        )
        self.assertEqual(self.client.post(self.url("create"), values).status_code, 302)
        events = list(EventSeries.objects.get().events.order_by("occurrence_index"))
        self.assertEqual(
            [(e.ends_at - e.starts_at).total_seconds() / 3600 for e in events], [24, 23, 24]
        )

    def test_dst_invalid_occurrence_and_unbounded_rule_create_nothing(self):
        for changes in (
            {"starts_at": "2026-03-22T02:30", "ends_at": "2026-03-22T04:00"},
            {"repeat_count": 101},
            {"repeat_interval": 13},
            {"repeat_frequency": "unlimited"},
            {"repeat_frequency": "monthly", "repeat_interval": 12, "repeat_count": 8},
        ):
            data = {**self.payload(repeat_frequency="weekly", repeat_count=4), **changes}
            response = self.client.post(self.url("create"), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(EventSeries.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 1)

    def test_individual_edit_creates_exception_preserved_by_series_change(self):
        series = self.create()
        event = series.events.get(occurrence_index=1)
        values = self.payload(
            title="Exception privée",
            version=event.version,
            submission_id=event.submission_id,
            starts_at="2026-10-15T18:00",
            ends_at="2026-10-15T19:00",
        )
        self.assertEqual(self.client.post(self.url("edit", event.pk), values).status_code, 302)
        event.refresh_from_db()
        self.assertTrue(event.is_exception)
        self.assertEqual(
            self.client.post(self.series_url(series), self.series_payload(series)).status_code, 409
        )
        series.refresh_from_db()
        self.assertEqual(
            self.client.post(
                self.series_url(series), self.series_payload(series, title="Série modifiée")
            ).status_code,
            302,
        )
        event.refresh_from_db()
        self.assertEqual(event.title, "Exception privée")
        self.assertEqual(series.events.filter(title="Série modifiée").count(), 3)

    def test_rule_edit_shorten_and_extend_preserves_identity_and_archived_exception(self):
        series = self.create()
        exception = series.events.get(occurrence_index=1)
        self.assertEqual(
            self.client.post(
                self.url("archive", exception.pk), {"version": exception.version, "confirm": "on"}
            ).status_code,
            302,
        )
        series.refresh_from_db()
        self.assertEqual(
            self.client.post(
                self.series_url(series), {**self.series_payload(series), "repeat_count": 2}
            ).status_code,
            302,
        )
        self.assertEqual(series.events.filter(deleted_at__isnull=False).count(), 3)
        series.refresh_from_db()
        self.assertEqual(
            self.client.post(
                self.series_url(series), {**self.series_payload(series), "repeat_count": 5}
            ).status_code,
            302,
        )
        exception.refresh_from_db()
        self.assertIsNotNone(exception.deleted_at)
        self.assertTrue(exception.is_exception)
        self.assertEqual(series.events.count(), 5)

    def test_expansion_needs_add_and_never_grants_publication(self):
        series = self.create()
        self.role.permissions.remove(
            Permission.objects.get(content_type__app_label="planning", codename="add_event")
        )
        response = self.client.post(
            self.series_url(series), {**self.series_payload(series), "repeat_count": 5}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="planning", codename="add_event")
        )
        self.role.permissions.remove(
            Permission.objects.get(content_type__app_label="planning", codename="publish_event")
        )
        self.assertEqual(
            self.client.post(
                self.series_url(series), {**self.series_payload(series), "repeat_count": 5}
            ).status_code,
            302,
        )
        self.assertFalse(series.events.get(occurrence_index=4).is_online)
        self.assertTrue(series.events.get(occurrence_index=0).is_online)

    def test_series_site_permission_csrf_and_transaction(self):
        series = self.create()
        other_url = reverse("planning:series-edit", args=[self.other.pk, series.pk])
        self.assertEqual(self.client.get(other_url).status_code, 404)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(
            csrf.post(self.series_url(series), self.series_payload(series)).status_code, 403
        )
        with (
            patch("apps.planning.series_views.audit", side_effect=DatabaseError("audit failure")),
            self.assertRaises(DatabaseError),
        ):
            self.client.post(
                self.series_url(series), self.series_payload(series, title="Rolled back")
            )
        self.assertFalse(Event.objects.filter(title="Rolled back").exists())
        series.refresh_from_db()
        self.assertEqual(series.version, 1)

    def test_creating_series_rolls_back_every_occurrence_on_audit_error(self):
        with (
            patch("apps.planning.views.audit", side_effect=DatabaseError("audit failure")),
            self.assertRaises(DatabaseError),
        ):
            self.client.post(
                self.url("create"), self.payload(repeat_frequency="daily", repeat_count=3)
            )
        self.assertEqual(EventSeries.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 1)

    def test_move_preview_does_not_write_then_post_preserves_duration(self):
        response = self.client.get(
            self.url("move", self.event.pk),
            {"date": "2026-10-07", "hour": 17, "timezone": "Europe/Paris", "version": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["hour"], "17:00")
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at, self.start)
        values = {
            "date": "2026-10-07",
            "hour": "17:00",
            "timezone": "Europe/Paris",
            "version": 1,
            "confirm": "on",
        }
        self.assertEqual(self.client.post(self.url("move", self.event.pk), values).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at, datetime(2026, 10, 7, 15, tzinfo=UTC))
        self.assertEqual((self.event.ends_at - self.event.starts_at).total_seconds(), 7200)
        self.assertEqual(self.client.post(self.url("move", self.event.pk), values).status_code, 409)
        self.assertEqual(list(self.event.resources.all()), [self.resource])

    def test_moving_an_occurrence_changes_only_it_and_invalidates_series_form(self):
        series = self.create()
        event = series.events.get(occurrence_index=1)
        values = {
            "date": "2026-10-20",
            "hour": "17:00",
            "timezone": "Europe/Paris",
            "version": 1,
            "confirm": "on",
        }
        self.assertEqual(self.client.post(self.url("move", event.pk), values).status_code, 302)
        event.refresh_from_db()
        self.assertTrue(event.is_exception)
        self.assertEqual(series.events.filter(is_exception=False).count(), 3)
        self.assertEqual(
            self.client.post(self.series_url(series), self.series_payload(series)).status_code, 409
        )

    def test_invalid_move_permissions_csrf_and_audit_rollback(self):
        url = self.url("move", self.event.pk)
        values = {
            "date": "2026-03-29",
            "hour": "02:30",
            "timezone": "Europe/Paris",
            "version": 1,
            "confirm": "on",
        }
        self.assertEqual(self.client.post(url, values).status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at, self.start)
        self.assertEqual(
            self.client.get(self.url("move", self.event.pk, site=self.other)).status_code, 404
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(url, values).status_code, 403)
        valid = {**values, "date": "2026-10-07", "hour": "12:00"}
        with (
            patch("apps.planning.series_views.audit", side_effect=DatabaseError("audit failure")),
            self.assertRaises(DatabaseError),
        ):
            self.client.post(url, valid)
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at, self.start)
        self.role.permissions.remove(
            Permission.objects.get(content_type__app_label="planning", codename="change_event")
        )
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get(self.url("calendar")), "data-planning-move")
