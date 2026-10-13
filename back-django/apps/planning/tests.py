from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.tenancy.models import Role, Site, SiteMembership

from .forms import EventForm
from .models import Event, Resource


class PlanningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code="planning-tests", name="Planning tests")
        cls.other = Site.objects.create(code="planning-other", name="Other")
        cls.user = get_user_model().objects.create_user(username="planner-test")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.role = Role.objects.create(code="planner-test", name="Planner")
        cls.role.permissions.add(*Permission.objects.filter(content_type__app_label="planning"))
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        cls.resource = Resource.objects.create(site=cls.site, name="Scène")
        cls.start = datetime(2026, 10, 3, 12, tzinfo=UTC)
        cls.event = Event.objects.create(
            site=cls.site,
            title="Concert test",
            kind="concert",
            starts_at=cls.start,
            ends_at=cls.start + timedelta(hours=2),
            is_online=True,
        )
        cls.event.resources.add(cls.resource)

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, name, pk=None, site=None):
        return reverse(
            f"planning:{name}",
            args=[(site or self.site).pk, pk] if pk else [(site or self.site).pk],
        )

    def payload(self, **kwargs):
        return {
            "title": "Webinaire test",
            "kind": "webinar",
            "timezone": "Europe/Paris",
            "starts_at": "2026-10-03T16:00",
            "ends_at": "2026-10-03T17:00",
            "submission_id": str(uuid4()),
            "resources": [self.resource.pk],
            "is_online": "on",
            **kwargs,
        }

    def filters(self, **kwargs):
        return {"start": "2026-10-01T00:00:00Z", "end": "2026-11-01T00:00:00Z", **kwargs}

    def test_hourly_view_renders_safe_minute_geometry_without_mutating_events(self):
        self.event.title = '<img src=x onerror="alert(1)">'
        self.event.starts_at = datetime(2026, 10, 5, 8, 15, tzinfo=UTC)
        self.event.ends_at = self.event.starts_at + timedelta(minutes=90)
        self.event.save()
        response = self.client.get(
            self.url("calendar"),
            {"date": "2026-10-05", "view": "day", "timezone": "Europe/Paris"},
        )
        self.assertContains(response, 'data-offset="615.0"')
        self.assertContains(response, 'data-duration="90.0"')
        self.assertContains(response, "&lt;img src=x")
        self.assertNotContains(response, '<img src=x onerror="alert(1)">')
        self.assertNotContains(response, 'style="')
        self.event.refresh_from_db()
        self.assertEqual(self.event.version, 1)
        self.assertIn("style-src 'self'", response["Content-Security-Policy"])

    def test_create_publish_edit_archive_restore_roundtrip(self):
        response = self.client.post(
            self.url("create"), self.payload(description="<script>alert(1)</script>")
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title="Webinaire test")
        self.assertEqual(event.starts_at.hour, 14)
        self.assertEqual(list(event.resources.all()), [self.resource])
        self.assertContains(self.client.get(self.url("detail", event.pk)), "&lt;script&gt;")
        changed = self.payload(
            title="Webinaire modifié", version=event.version, submission_id=event.submission_id
        )
        self.assertEqual(self.client.post(self.url("edit", event.pk), changed).status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.version, 2)
        self.assertEqual(self.client.post(self.url("edit", event.pk), changed).status_code, 409)
        self.assertEqual(
            self.client.post(
                self.url("archive", event.pk), {"version": 2, "confirm": "on"}
            ).status_code,
            302,
        )
        self.assertEqual(self.client.get(self.url("api-event", event.pk)).status_code, 404)
        self.assertContains(self.client.get(self.url("trash")), event.title)
        self.assertEqual(
            self.client.post(
                self.url("restore", event.pk), {"version": 3, "confirm": "on"}
            ).status_code,
            302,
        )
        event.refresh_from_db()
        self.assertIsNone(event.deleted_at)
        self.assertFalse(event.is_online)
        self.assertEqual(
            AuditEvent.objects.filter(
                entity_type="planning.event", entity_id=str(event.pk)
            ).count(),
            4,
        )

    def test_all_scheduler_views_and_invalid_filters(self):
        for view in ("day", "week", "workweek", "month", "agenda", "timeline"):
            with self.subTest(view=view):
                response = self.client.get(
                    self.url("calendar"), {"view": view, "date": "2026-10-03"}
                )
                self.assertEqual(response.status_code, 200)
                if view != "workweek":
                    self.assertContains(response, self.event.title)
                self.assertContains(response, "Europe/Paris")
                self.assertIn("default-src 'none'", response["Content-Security-Policy"])
                self.assertIn("no-store", response["Cache-Control"])
        for values in (
            {"date": "invalid"},
            {"timezone": "bad/zone"},
            {"resource": "99999"},
            {"view": "invalid"},
        ):
            self.assertEqual(self.client.get(self.url("calendar"), values).status_code, 200)

    def test_private_site_permissions_and_disabled_module(self):
        other_event = Event.objects.create(
            site=self.other,
            title="Secret",
            starts_at=self.start,
            ends_at=self.start + timedelta(hours=1),
        )
        self.assertEqual(self.client.get(self.url("detail", other_event.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("calendar", site=self.other)).status_code, 404)
        self.role.permissions.clear()
        self.assertEqual(self.client.get(self.url("calendar")).status_code, 404)
        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="planning", codename="view_event")
        )
        self.assertEqual(self.client.post(self.url("create"), self.payload()).status_code, 404)
        self.site.planning_enabled = False
        self.site.save()
        self.assertEqual(self.client.get(self.url("calendar")).status_code, 404)
        self.assertEqual(self.client.get(self.url("api-events"), self.filters()).status_code, 404)

    def test_no_publish_permission_and_csrf(self):
        self.role.permissions.remove(
            Permission.objects.get(content_type__app_label="planning", codename="publish_event")
        )
        self.assertEqual(self.client.post(self.url("create"), self.payload()).status_code, 302)
        self.assertFalse(Event.objects.get(title="Webinaire test").is_online)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.url("create"), self.payload()).status_code, 403)

    def test_invalid_dates_timezone_link_resources_and_all_day(self):
        foreign = Resource.objects.create(site=self.other, name="Other")
        for changes in (
            {"ends_at": "2026-10-03T15:00"},
            {"timezone": "Invalid/Timezone"},
            {"link": "javascript:alert(1)"},
            {"resources": [foreign.pk]},
            {"all_day": "on"},
            {"starts_at": "2026-03-29T02:30", "ends_at": "2026-03-29T05:00"},
            {"starts_at": "2026-10-25T02:30", "ends_at": "2026-10-25T05:00"},
            {"starts_at": "broken"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post(self.url("create"), self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertEqual(Event.objects.count(), 1)
        form = EventForm(
            self.payload(starts_at="2026-10-03T00:00", ends_at="2026-10-04T00:00", all_day="on"),
            site=self.site,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_repeat_submission_and_audit_rollback(self):
        payload = self.payload()
        for _ in range(2):
            self.assertEqual(self.client.post(self.url("create"), payload).status_code, 302)
        self.assertEqual(Event.objects.filter(title="Webinaire test").count(), 1)
        with (
            patch("apps.planning.views.audit", side_effect=DatabaseError("test audit")),
            self.assertRaises(DatabaseError),
        ):
            self.client.post(self.url("create"), self.payload(title="Rollback"))
        self.assertFalse(Event.objects.filter(title="Rollback").exists())

    def test_public_range_filters_and_no_drafts_or_other_site(self):
        Event.objects.create(
            site=self.site,
            title="Draft",
            starts_at=self.start,
            ends_at=self.start + timedelta(hours=1),
        )
        Event.objects.create(
            site=self.other,
            title="Other",
            starts_at=self.start,
            ends_at=self.start + timedelta(hours=1),
            is_online=True,
        )
        self.client.logout()
        response = self.client.get(
            self.url("api-events"), self.filters(kind="concert", resource_id=self.resource.pk)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e["id"] for e in response.json()["results"]], [self.event.pk])
        self.assertNotIn("version", response.json()["results"][0])
        response = self.client.get(
            self.url("api-events"), self.filters(start=self.event.ends_at.isoformat())
        )
        self.assertEqual(response.json()["count"], 0)
        self.site.public_access_enabled = False
        self.site.save()
        self.assertEqual(self.client.get(self.url("api-event", self.event.pk)).status_code, 404)

    def test_api_errors_and_pagination(self):
        for changes in (
            {"start": "broken"},
            {"end": "2027-10-01T00:00:00Z"},
            {"kind": "bad"},
            {"resource_id": "bad"},
            {"page_size": "101"},
            {"page": "999"},
        ):
            self.assertEqual(
                self.client.get(self.url("api-events"), self.filters(**changes)).status_code, 400
            )
        response = self.client.post(self.url("api-events"), {})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")

    def test_resources_permissions_versions_and_duplicate_name(self):
        response = self.client.post(self.url("resources"), {"name": "Salle", "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        r = Resource.objects.get(name="Salle")
        values = {"name": "Salle modifiée", "version": r.version}
        self.assertEqual(self.client.post(self.url("resource-edit", r.pk), values).status_code, 302)
        self.assertEqual(self.client.post(self.url("resource-edit", r.pk), values).status_code, 409)
        self.assertEqual(
            self.client.post(self.url("resources"), {"name": "Salle modifiée"}).status_code, 200
        )

    def test_database_duration_and_reserved_site(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.create(
                site=self.site, title="Invalid", starts_at=self.start, ends_at=self.start
            )
        with self.assertRaises(ValidationError):
            Event(
                site_id=1,
                title="Owner",
                starts_at=self.start,
                ends_at=self.start + timedelta(hours=1),
            ).full_clean()

    def test_reused_submission_with_changed_payload_or_other_site_is_conflict(self):
        data = self.payload()
        self.assertEqual(self.client.post(self.url("create"), data).status_code, 302)
        self.assertEqual(
            self.client.post(self.url("create"), {**data, "title": "Different"}).status_code, 409
        )
        foreign = Event.objects.create(
            site=self.other,
            title="Foreign",
            starts_at=self.start,
            ends_at=self.start + timedelta(hours=1),
        )
        self.assertEqual(
            self.client.post(
                self.url("create"), self.payload(submission_id=foreign.submission_id)
            ).status_code,
            409,
        )

    def test_slot_prefills_form_and_inactive_resources_cannot_be_newly_assigned(self):
        response = self.client.get(
            self.url("create"), {"date": "2026-10-07", "hour": "15", "timezone": "America/New_York"}
        )
        self.assertEqual(response.context["form"].initial["starts_at"], "2026-10-07T15:00")
        self.resource.is_active = False
        self.resource.save()
        response = self.client.post(self.url("create"), self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn("resources", response.context["form"].errors)
        existing = EventForm(instance=self.event, site=self.site)
        self.assertIn(self.resource, existing.fields["resources"].queryset)

    def test_public_api_requires_explicit_offset_and_handles_empty_foreign_resource(self):
        self.assertEqual(
            self.client.get(
                self.url("api-events"), self.filters(start="2026-10-01T00:00")
            ).status_code,
            400,
        )
        r = Resource.objects.create(site=self.other, name="Outside")
        self.assertEqual(
            self.client.get(self.url("api-events"), self.filters(resource_id=r.pk)).json()["count"],
            0,
        )
