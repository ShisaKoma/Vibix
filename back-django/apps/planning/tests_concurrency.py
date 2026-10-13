from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connections
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.tenancy.models import Role, Site, SiteMembership

from .models import Event


class ConcurrentPlanningTests(TransactionTestCase):
    def setUp(self):
        self.site = Site.objects.create(code="concurrent-events", name="Concurrent")
        self.user = get_user_model().objects.create_user(username="concurrent-planner")
        UserProfile.objects.create(user=self.user, first_run_completed_at=timezone.now())
        role = Role.objects.create(code="concurrent-role", name="Planner")
        role.permissions.add(*Permission.objects.filter(content_type__app_label="planning"))
        SiteMembership.objects.create(user=self.user, site=self.site, role=role)
        self.payload = {
            "title": "Simultaneous event",
            "kind": "event",
            "timezone": "Europe/Paris",
            "starts_at": "2026-10-03T14:00",
            "ends_at": "2026-10-03T15:00",
            "submission_id": str(uuid4()),
        }

    def parallel_post(self, url, payload):
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.user)
                barrier.wait(timeout=10)
                return client.post(url, payload).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(submit) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_simultaneous_repeated_creation_is_recorded_once(self):
        url = reverse("planning:create", args=[self.site.pk])
        self.assertEqual(self.parallel_post(url, self.payload), [302, 302])
        self.assertEqual(Event.objects.filter(site=self.site).count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(site=self.site, action="planning.event.created").count(), 1
        )

    def test_simultaneous_edits_reject_one_stale_version(self):
        start = datetime(2026, 10, 3, 12, tzinfo=UTC)
        event = Event.objects.create(
            site=self.site, title="Concurrent", starts_at=start, ends_at=start + timedelta(hours=1)
        )
        payload = {
            **self.payload,
            "submission_id": str(event.submission_id),
            "version": event.version,
        }
        url = reverse("planning:edit", args=[self.site.pk, event.pk])
        self.assertEqual(sorted(self.parallel_post(url, payload)), [302, 409])
        event.refresh_from_db()
        self.assertEqual(event.version, 2)
