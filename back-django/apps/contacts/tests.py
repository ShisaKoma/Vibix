import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.addressbook.models import ContactPoint, Entry, EntryType
from apps.audit.models import AuditEvent
from apps.configuration.models import SiteCommunicationSettings
from apps.tenancy.models import Role, Site, SiteMembership

from .models import APIClient, ContactRequest, Notification, QuotaBucket
from .notifications import claim_notification, deliver_notification
from .security import MAX_BODY_BYTES, consume_quota, digest


class ContactsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(
            code="contacts-test", name="Site Contacts", contacts_enabled=True
        )
        cls.other = Site.objects.create(code="contacts-other", name="Autre", contacts_enabled=True)
        cls.policy = SiteCommunicationSettings.objects.create(
            site=cls.site, spam_terms="credit facile", contact_recipient="internal@example.test"
        )
        cls.user = get_user_model().objects.create_user(username="contact-agent")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.role = Role.objects.create(code="contact-agent", name="Agent")
        cls.role.permissions.add(
            *Permission.objects.filter(content_type__app_label__in=["contacts", "addressbook"])
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        cls.entry_type = EntryType.objects.create(
            site=cls.site, kind="person", code="person", label="Personne"
        )
        cls.api_token = "ct_test_only_token"
        cls.api_client = APIClient.objects.create(
            site=cls.site,
            name="Intégration",
            token_hash=digest(cls.api_token),
            expires_at=timezone.now() + timedelta(days=1),
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.browser = Client(enforce_csrf_checks=True)
        self.payload = {
            "name": "Exemple",
            "first_name": "Camille",
            "email": "camille@example.test",
            "phone": "",
            "organization": "",
            "title": "Renseignements",
            "message": "Bonjour, pouvez-vous préciser votre offre ?",
        }
        self.submit_url = reverse("contacts:submit", args=[self.site.pk])
        self.now = time.time()
        with patch("apps.contacts.security.time.time", return_value=self.now):
            self.challenge = self.browser.get(
                reverse("contacts:challenge", args=[self.site.pk])
            ).json()

    def web_post(self, **changes):
        data = {**self.payload, "challenge": self.challenge["challenge"], **changes}
        with patch("apps.contacts.security.time.time", return_value=self.now + 3):
            return self.browser.post(
                self.submit_url,
                json.dumps(data),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=self.challenge["csrf_token"],
            )

    def api_post(self, *, site=None, token=None, key=None, **changes):
        return self.browser.post(
            reverse("contacts:submit", args=[(site or self.site).pk]),
            json.dumps({**self.payload, **changes}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token or self.api_token}",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()),
        )

    def contact(self, **changes):
        return ContactRequest.objects.create(
            site=changes.pop("site", self.site),
            **{
                **self.payload,
                "source": "web",
                "status": "accepted",
                "receipt_hash": digest(str(uuid.uuid4())),
                "payload_hash": "a" * 64,
                **changes,
            },
        )

    def treatment_url(self, contact):
        return reverse("contacts:detail", args=[self.site.pk, contact.pk])

    def link_data(self, contact, **changes):
        return {
            "mode": "create",
            "version": contact.updated_at.isoformat(),
            "entry_type": self.entry_type.pk,
            "confirm": "on",
            "add_email": "on",
            "add_phone": "on",
            **changes,
        }

    def test_public_reception_is_private_audited_and_queued(self):
        response = self.web_post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "accepted"})
        contact = ContactRequest.objects.get(site=self.site)
        self.assertEqual(contact.status, "new")
        self.assertEqual(contact.notification.state, "pending")
        self.assertTrue(
            AuditEvent.objects.filter(site=self.site, action="contacts.receive").exists()
        )
        self.assertEqual(self.browser.get(self.submit_url).status_code, 405)
        self.assertEqual(
            self.browser.get(
                reverse("contacts:detail", args=[self.site.pk, contact.pk])
            ).status_code,
            302,
        )
        self.assertEqual(self.client.get(self.treatment_url(contact)).status_code, 200)

    def test_csrf_origin_cookie_and_challenge_are_required(self):
        raw = json.dumps({**self.payload, "challenge": self.challenge["challenge"]})
        self.assertEqual(
            self.browser.post(self.submit_url, raw, content_type="application/json").status_code,
            403,
        )
        with patch("apps.contacts.security.time.time", return_value=self.now + 3):
            self.assertEqual(
                self.browser.post(
                    self.submit_url,
                    raw,
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=self.challenge["csrf_token"],
                    HTTP_ORIGIN="https://evil.example.test",
                ).status_code,
                403,
            )
        self.assertEqual(self.web_post(challenge="forged").status_code, 400)
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())

    def test_challenge_site_ip_minimum_delay_and_expiry(self):
        raw = json.dumps({**self.payload, "challenge": self.challenge["challenge"]})
        for delay, remote in ((0, "127.0.0.1"), (1801, "127.0.0.1"), (3, "203.0.113.9")):
            with (
                self.subTest(delay=delay, remote=remote),
                patch("apps.contacts.security.time.time", return_value=self.now + delay),
            ):
                response = self.browser.post(
                    self.submit_url,
                    raw,
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=self.challenge["csrf_token"],
                    REMOTE_ADDR=remote,
                )
                self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())

    def test_duplicate_submission_is_idempotent_and_changed_payload_conflicts(self):
        self.assertEqual(self.web_post().status_code, 202)
        self.assertEqual(self.web_post().status_code, 202)
        self.assertEqual(self.web_post(title="Autre objet").status_code, 409)
        self.assertEqual(ContactRequest.objects.filter(site=self.site).count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_spam_title_and_honeypot_do_not_notify(self):
        for changes in ({"title": "CRÉDIT FACILE"}, {"website": "bot"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.api_post(**changes).status_code, 202)
        self.assertEqual(ContactRequest.objects.filter(site=self.site, status="spam").count(), 2)
        self.assertFalse(Notification.objects.exists())

    def test_quota_counts_invalid_payloads_and_returns_retry_after(self):
        self.policy.contact_ip_hourly_limit = 2
        self.policy.save()
        self.assertEqual(self.web_post(name="").status_code, 400)
        self.assertEqual(self.web_post(title="").status_code, 400)
        response = self.web_post()
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())

    def test_site_quota_applies_across_distinct_ips_and_forwarded_ip_is_ignored(self):
        self.policy.contact_hourly_limit = self.policy.contact_ip_hourly_limit = 1
        self.policy.save()
        self.assertEqual(self.api_post().status_code, 202)
        response = self.browser.post(
            self.submit_url,
            json.dumps(self.payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_token}",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_FORWARDED_FOR="203.0.113.11",
        )
        self.assertEqual(response.status_code, 429)
        self.assertTrue(
            QuotaBucket.objects.filter(
                site=self.site, scope="submit", identity="site", count=1
            ).exists()
        )

    def test_strict_schema_body_limits_and_header_fields(self):
        for changes in (
            {"status": "accepted"},
            {"email": "invalid"},
            {"phone": "abc"},
            {"name": ["array"]},
            {"title": "A\nB"},
            {"message": "x" * 10001},
        ):
            with self.subTest(changes=changes):
                # Use a distinct IP per invalid request to test validation, not quota.
                self.policy.contact_ip_hourly_limit = 100
                self.policy.save()
                self.assertEqual(self.api_post(**changes).status_code, 400)
        response = self.browser.post(
            self.submit_url,
            b"x",
            content_type="application/json",
            CONTENT_LENGTH=str(MAX_BODY_BYTES + 1),
        )
        self.assertEqual(response.status_code, 413)
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())

    def test_api_token_scope_expiry_revocation_and_uuid_idempotency(self):
        key = str(uuid.uuid4())
        self.assertEqual(self.api_post(key=key).status_code, 202)
        self.assertEqual(self.api_post(key=key).status_code, 202)
        self.assertEqual(self.api_post(key="bad").status_code, 400)
        self.assertEqual(self.api_post(site=self.other).status_code, 401)
        APIClient.objects.filter(pk=self.api_client.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.api_post().status_code, 401)
        APIClient.objects.filter(pk=self.api_client.pk).update(
            revoked_at=None, expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.browser.post(
            self.submit_url,
            json.dumps(self.payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_token}",
            REMOTE_ADDR="203.0.113.20",
        )
        self.assertEqual(response.status_code, 401)

    def test_disabled_site_module_public_access_and_reserved_site_refused(self):
        for field in ("contacts_enabled", "public_access_enabled", "is_active"):
            Site.objects.filter(pk=self.site.pk).update(**{field: False})
            self.assertEqual(self.api_post().status_code, 404)
            self.assertEqual(
                self.browser.get(reverse("contacts:challenge", args=[self.site.pk])).status_code,
                404,
            )
            Site.objects.filter(pk=self.site.pk).update(**{field: True})
        self.assertEqual(
            self.browser.post(
                reverse("contacts:submit", args=[1]), "{}", content_type="application/json"
            ).status_code,
            404,
        )

    def test_reception_rolls_back_contact_and_outbox_on_audit_failure(self):
        with patch("apps.contacts.reception.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.api_post()
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())
        self.assertFalse(Notification.objects.exists())
        self.assertTrue(
            QuotaBucket.objects.filter(site=self.site, scope="submit", count=1).exists()
        )

    def test_native_public_form_is_connected_and_escapes_errors(self):
        response = self.browser.get(reverse("contacts:public-form", args=[self.site.pk]))
        token = response.context["form"].initial["challenge"]
        with patch("apps.contacts.security.time.time", return_value=time.time() + 3):
            from urllib.parse import urlencode

            response = self.browser.post(
                reverse("contacts:public-form", args=[self.site.pk]),
                urlencode(
                    {
                        **self.payload,
                        "challenge": token,
                        "csrfmiddlewaretoken": self.browser.cookies["csrftoken"].value,
                    }
                ),
                content_type="application/x-www-form-urlencoded",
            )
        self.assertContains(response, "Votre demande a été reçue", status_code=202)
        self.assertEqual(ContactRequest.objects.filter(site=self.site).count(), 1)

    def test_treatment_note_transition_audit_and_stale_version(self):
        contact = self.contact(status="new")
        url = self.treatment_url(contact)
        self.assertEqual(
            self.client.post(
                url, {"version": contact.updated_at.isoformat(), "status": "in_progress"}
            ).status_code,
            200,
        )
        response = self.client.post(
            url,
            {
                "version": contact.updated_at.isoformat(),
                "status": "accepted",
                "note": "<script>alert(1)</script>",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.post(
                url, {"version": contact.updated_at.isoformat(), "status": "closed"}
            ).status_code,
            409,
        )
        contact.refresh_from_db()
        self.assertEqual(contact.status, "accepted")
        self.assertEqual(contact.notes.count(), 1)
        response = self.client.get(url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertTrue(AuditEvent.objects.filter(action="contacts.treat", site=self.site).exists())

    def test_backoffice_isolation_revoked_permission_and_csrf(self):
        contact = self.contact()
        outside = self.contact(site=self.other)
        self.assertEqual(
            self.client.get(
                reverse("contacts:detail", args=[self.other.pk, outside.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("contacts:detail", args=[self.site.pk, outside.pk])
            ).status_code,
            404,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(
            csrf_client.post(self.treatment_url(contact), {"status": "closed"}).status_code, 403
        )
        self.role.permissions.clear()
        self.assertEqual(self.client.get(self.treatment_url(contact)).status_code, 404)

    def test_archive_restore_retains_notes_and_cancels_notification(self):
        contact = self.contact()
        Notification.objects.create(contact=contact)
        response = self.client.post(
            reverse("contacts:archive", args=[self.site.pk, contact.pk]),
            {"version": contact.updated_at.isoformat(), "confirm": "on"},
        )
        self.assertEqual(response.status_code, 302)
        contact.refresh_from_db()
        self.assertIsNotNone(contact.deleted_at)
        self.assertEqual(contact.notification.state, "cancelled")
        response = self.client.post(
            reverse("contacts:restore", args=[self.site.pk, contact.pk]),
            {"version": contact.updated_at.isoformat(), "confirm": "on"},
        )
        self.assertEqual(response.status_code, 302)
        contact.refresh_from_db()
        self.assertIsNone(contact.deleted_at)
        self.assertEqual(contact.notification.state, "pending")

    def test_spam_release_queues_notification(self):
        contact = self.contact(status="spam")
        self.assertEqual(
            self.client.post(
                self.treatment_url(contact),
                {"version": contact.updated_at.isoformat(), "status": "accepted"},
            ).status_code,
            302,
        )
        self.assertEqual(Notification.objects.get(contact=contact).state, "pending")

    def test_addressbook_creation_is_explicit_audited_and_idempotent(self):
        contact = self.contact(phone="+33 1 23 45 67 89")
        url = reverse("contacts:addressbook", args=[self.site.pk, contact.pk])
        data = self.link_data(contact)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.addressbook_entry.site_id, self.site.pk)
        self.assertEqual(contact.addressbook_entry.contacts.count(), 2)
        self.assertTrue(AuditEvent.objects.filter(action="contacts.addressbook.create").exists())
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(Entry.objects.filter(site=self.site).count(), 1)

    def test_addressbook_sync_previews_differences_preserves_existing_contacts(self):
        contact = self.contact(phone="+33 1 23 45 67 89")
        entry = Entry.objects.create(
            site=self.site,
            kind="person",
            entry_type=self.entry_type,
            name="Ancien",
            first_name="Prénom",
        )
        existing = ContactPoint.objects.create(
            entry=entry, mode="phone", value="+33 1 11 22 33 44", is_primary=True
        )
        url = reverse("contacts:addressbook", args=[self.site.pk, contact.pk])
        response = self.client.get(url, {"entry": entry.pk})
        self.assertContains(response, "Ancien")
        self.assertContains(response, existing.value)
        response = self.client.post(
            url,
            self.link_data(
                contact,
                mode="sync",
                entry=entry.pk,
                entry_version=entry.updated_at.isoformat(),
                update_name="on",
            ),
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        existing.refresh_from_db()
        self.assertEqual(entry.name, "Exemple")
        self.assertTrue(existing.is_primary)
        self.assertEqual(entry.contacts.filter(mode="phone").count(), 2)
        self.assertEqual(entry.contacts.filter(mode="email").count(), 1)

    def test_addressbook_duplicates_inactive_module_and_permissions_block_writes(self):
        contact = self.contact()
        entry = Entry.objects.create(
            site=self.site, kind="person", entry_type=self.entry_type, name="Personne"
        )
        ContactPoint.objects.create(entry=entry, mode="email", value=contact.email)
        url = reverse("contacts:addressbook", args=[self.site.pk, contact.pk])
        self.assertEqual(self.client.post(url, self.link_data(contact)).status_code, 200)
        self.assertEqual(Entry.objects.filter(site=self.site).count(), 1)
        self.role.permissions.remove(
            Permission.objects.get(content_type__app_label="addressbook", codename="change_entry")
        )
        response = self.client.post(
            url,
            self.link_data(
                contact, mode="sync", entry=entry.pk, entry_version=entry.updated_at.isoformat()
            ),
        )
        self.assertEqual(response.status_code, 404)
        Site.objects.filter(pk=self.site.pk).update(addressbook_enabled=False)
        self.assertEqual(self.client.get(url).status_code, 404)
        contact.refresh_from_db()
        self.assertIsNone(contact.addressbook_entry_id)

    def test_addressbook_requires_validated_contact_and_current_target_version(self):
        contact = self.contact(status="spam")
        url = reverse("contacts:addressbook", args=[self.site.pk, contact.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        ContactRequest.objects.filter(pk=contact.pk).update(status="accepted")
        contact.refresh_from_db()
        entry = Entry.objects.create(
            site=self.site, kind="person", entry_type=self.entry_type, name="Personne"
        )
        self.assertEqual(
            self.client.post(
                url, self.link_data(contact, mode="link", entry=entry.pk, entry_version="old")
            ).status_code,
            409,
        )

    def test_client_management_shows_secret_once_and_revocation_invalidates(self):
        url = reverse("contacts:clients", args=[self.site.pk])
        response = self.client.post(url, {"name": "Serveur interne", "expires_in_days": 30})
        self.assertEqual(response.status_code, 200)
        token = response.context["token"]
        client = APIClient.objects.get(token_hash=digest(token))
        self.assertNotEqual(client.token_hash, token)
        self.assertNotContains(self.client.get(url), token)
        self.assertEqual(self.api_post(token=token).status_code, 202)
        self.assertEqual(
            self.client.post(
                reverse("contacts:client-revoke", args=[self.site.pk, client.pk]),
                {"version": str(client.pk), "confirm": "on"},
            ).status_code,
            302,
        )
        self.assertEqual(self.api_post(token=token).status_code, 401)

    def test_notification_is_internal_fixed_header_and_claimed_once(self):
        contact = self.contact()
        Notification.objects.create(contact=contact)
        job = claim_notification(self.site.pk)
        self.assertIsNone(claim_notification(self.site.pk))
        with patch(
            "apps.contacts.notifications.smtp_connection",
            return_value=mail.get_connection("django.core.mail.backends.locmem.EmailBackend"),
        ):
            self.assertEqual(deliver_notification(job), "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["internal@example.test"])
        self.assertNotIn(contact.message, mail.outbox[0].body)
        self.assertNotIn(contact.email, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].subject, "Nouvelle demande de contact")
        self.assertEqual(deliver_notification(job), "cancelled")
        self.assertEqual(len(mail.outbox), 1)

    def test_notification_failure_is_sanitized_retried_then_failed(self):
        contact = self.contact()
        notification = Notification.objects.create(contact=contact)
        for attempt in range(5):
            Notification.objects.filter(pk=notification.pk).update(next_attempt_at=timezone.now())
            job = claim_notification(self.site.pk)
            with patch(
                "apps.contacts.notifications.smtp_connection", side_effect=OSError("private-secret")
            ):
                result = deliver_notification(job)
            self.assertEqual(result, "failed" if attempt == 4 else "pending")
        self.assertNotContains(self.client.get(self.treatment_url(contact)), "private-secret")
        contact.refresh_from_db()
        response = self.client.post(
            reverse("contacts:notification-retry", args=[self.site.pk, contact.pk]),
            {"version": contact.updated_at.isoformat(), "confirm": "on"},
        )
        self.assertEqual(response.status_code, 302)
        notification.refresh_from_db()
        self.assertEqual(notification.attempts, 0)
        self.assertEqual(notification.state, "pending")

    def test_notification_spam_after_claim_suspends_without_sending(self):
        contact = self.contact()
        Notification.objects.create(contact=contact)
        job = claim_notification(self.site.pk)
        ContactRequest.objects.filter(pk=contact.pk).update(status="spam")
        with patch("apps.contacts.notifications.smtp_connection") as smtp:
            self.assertEqual(deliver_notification(job), "cancelled")
            smtp.assert_not_called()

    def test_retention_preview_then_purge_preserves_carnet(self):
        entry = Entry.objects.create(
            site=self.site, kind="person", entry_type=self.entry_type, name="Conservée"
        )
        expired = self.contact(
            created_at=timezone.now() - timedelta(days=91), addressbook_entry=entry
        )
        fresh = self.contact()
        call_command("purge_contacts", site_code=self.site.code, stdout=StringIO())
        self.assertTrue(ContactRequest.objects.filter(pk=expired.pk).exists())
        call_command("purge_contacts", site_code=self.site.code, confirm=True, stdout=StringIO())
        self.assertFalse(ContactRequest.objects.filter(pk=expired.pk).exists())
        self.assertTrue(ContactRequest.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(Entry.objects.filter(pk=entry.pk).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action="contacts.retention.purge", entity_id=str(expired.pk)
            ).exists()
        )

    def test_client_creation_replay_does_not_create_another_access(self):
        url = reverse("contacts:clients", args=[self.site.pk])
        data = {"name": "Même client", "expires_in_days": 30}
        self.assertTrue(self.client.post(url, data).context["token"])
        response = self.client.post(url, data)
        self.assertIsNone(response.context["token"])
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(APIClient.objects.filter(site=self.site, name="Même client").count(), 1)

    def test_dedicated_proxy_header_requires_explicit_proxy_trust(self):
        from .security import remote_identity

        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="203.0.113.7",
            HTTP_X_CONTACTS_CLIENT_IP="198.51.100.8",
            HTTP_X_FORWARDED_FOR="198.51.100.9",
        )
        with override_settings(CONTACTS_TRUSTED_PROXY_IPS=[]):
            self.assertEqual(remote_identity(request), digest("203.0.113.7"))
        with override_settings(CONTACTS_TRUSTED_PROXY_IPS=["203.0.113.7"]):
            self.assertEqual(remote_identity(request), digest("198.51.100.8"))
            invalid_header = RequestFactory().get(
                "/",
                REMOTE_ADDR="203.0.113.7",
                HTTP_X_CONTACTS_CLIENT_IP="198.51.100.8, 198.51.100.9",
            )
            self.assertEqual(remote_identity(invalid_header), digest("203.0.113.7"))

    def test_exhausted_notification_lease_becomes_audited_failure(self):
        contact = self.contact()
        job = Notification.objects.create(
            contact=contact,
            state="processing",
            attempts=5,
            lease=uuid.uuid4(),
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIsNone(claim_notification(self.site.pk))
        job.refresh_from_db()
        self.assertEqual(job.state, "failed")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="contacts.notification.failed", entity_id=str(job.pk)
            ).exists()
        )

    def test_notification_queue_capacity_applies_to_restore_and_retry(self):
        from .reception import enqueue_notification

        waiting = self.contact()
        Notification.objects.create(contact=waiting)
        resumed = self.contact()
        job = Notification.objects.create(contact=resumed, state="cancelled")
        with patch("apps.contacts.reception.MAX_QUEUED_NOTIFICATIONS", 1):
            enqueue_notification(resumed)
            job.refresh_from_db()
            self.assertEqual(job.state, "failed")
            enqueue_notification(resumed, retry=True)
            job.refresh_from_db()
            self.assertEqual(job.state, "failed")
            Notification.objects.filter(contact=waiting).update(state="sent")
            enqueue_notification(resumed, retry=True)
            job.refresh_from_db()
            self.assertEqual(job.state, "pending")


class QuotaConcurrencyTests(TransactionTestCase):
    def test_two_workers_cannot_exceed_site_quota(self):
        site = Site.objects.create(code="quota-concurrency", name="Quota", contacts_enabled=True)
        SiteCommunicationSettings.objects.create(
            site=site, contact_hourly_limit=1, contact_ip_hourly_limit=1
        )
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.1")
                barrier.wait(timeout=10)
                return consume_quota(request, site)[0]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(
            QuotaBucket.objects.get(site=site, scope="submit", identity="site").count, 1
        )

    def test_notification_claim_is_exclusive_across_two_workers(self):
        site = Site.objects.create(
            code="notification-concurrency", name="Notification", contacts_enabled=True
        )
        contact = ContactRequest.objects.create(
            site=site,
            source="web",
            name="Exemple",
            email="person@example.test",
            title="Demande",
            message="Texte",
            receipt_hash="a" * 64,
            payload_hash="b" * 64,
        )
        Notification.objects.create(contact=contact)
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                job = claim_notification(site.pk)
                return job.pk if job else None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)
