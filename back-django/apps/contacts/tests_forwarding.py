import uuid
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core import mail
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import Site

from . import tests as reception_tests
from .models import ContactNote, ContactRequest, Forward
from .notifications import claim_forward, deliver_forward


class ForwardingTests(TestCase):
    setUp = reception_tests.ContactsTests.setUp
    contact = reception_tests.ContactsTests.contact
    treatment_url = reception_tests.ContactsTests.treatment_url

    @classmethod
    def setUpTestData(cls):
        reception_tests.ContactsTests.setUpTestData.__func__(cls)
        lookup = LookupList.objects.create(
            site=cls.site,
            module="contacts",
            kind="category",
            field_key="contacts_contactrequest.category",
            code="inbox",
            name="Classement",
        )
        cls.category = LookupEntry.objects.create(
            lookup_list=lookup, value="sales", label="Commercial"
        )

    def url(self, contact, name="forward", job=None, site=None):
        args = [(site or self.site).pk, contact.pk]
        if job:
            args.append(job.pk)
        return reverse(f"contacts:{name}", args=args)

    def forward_payload(self, contact, **changes):
        return {
            "version": contact.updated_at.isoformat(),
            "submission_id": str(uuid.uuid4()),
            "recipient": "reviewer@example.test",
            "introduction": "Pour traitement.",
            "confirm": "on",
            **changes,
        }

    def queue(self, contact=None, **changes):
        contact = contact or self.contact()
        with patch("apps.contacts.forwarding.smtp_connection"):
            response = self.client.post(self.url(contact), self.forward_payload(contact, **changes))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        return Forward.objects.get(contact=contact)

    def test_queue_is_explicit_idempotent_private_and_contains_no_notes(self):
        contact = self.contact(message="<script>alert(1)</script> Texte reçu")
        ContactNote.objects.create(
            contact=contact, author=self.user, text="Note interne confidentielle"
        )
        self.assertContains(self.client.get(self.url(contact)), "&lt;script&gt;")
        self.assertEqual(Forward.objects.count(), 0)
        values = self.forward_payload(contact)
        with patch("apps.contacts.forwarding.smtp_connection"):
            for _ in range(2):
                self.assertEqual(self.client.post(self.url(contact), values).status_code, 302)
        job = Forward.objects.get()
        self.assertIn(contact.message, job.body)
        self.assertIn(contact.email, job.body)
        self.assertIn("Pour traitement.", job.body)
        self.assertNotIn("Note interne confidentielle", job.body)
        self.assertEqual(AuditEvent.objects.filter(action="contacts.forward.queued").count(), 1)
        self.assertEqual(
            self.client.post(
                self.url(contact), {**values, "recipient": "other@example.test"}
            ).status_code,
            409,
        )

    def test_forward_requires_permissions_site_csrf_confirmation_and_fresh_version(self):
        contact = self.contact()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(
            strict.post(self.url(contact), self.forward_payload(contact)).status_code, 403
        )
        self.assertEqual(self.client.get(self.url(contact, site=self.other)).status_code, 404)
        for changes in ({"confirm": ""}, {"recipient": "a@example.test\r\nBcc: b@example.test"}):
            self.assertEqual(
                self.client.post(
                    self.url(contact), self.forward_payload(contact, **changes)
                ).status_code,
                200,
            )
        self.assertEqual(
            self.client.post(
                self.url(contact), self.forward_payload(contact, version="old")
            ).status_code,
            409,
        )
        self.assertEqual(Forward.objects.count(), 0)
        self.role.permissions.remove(
            Permission.objects.get(
                content_type__app_label="contacts", codename="forward_contactrequest"
            )
        )
        self.assertEqual(self.client.get(self.url(contact)).status_code, 404)
        self.assertNotContains(
            self.client.get(self.treatment_url(contact)), "Retransmettre le message"
        )

    def test_disabled_smtp_spam_and_trash_refuse_new_forward(self):
        contact = self.contact()
        self.assertContains(
            self.client.post(self.url(contact), self.forward_payload(contact)), "désactivé"
        )
        for state in ("spam", "accepted"):
            contact.status = state
            contact.deleted_at = timezone.now() if state == "accepted" else None
            contact.save()
            self.assertEqual(
                self.client.post(self.url(contact), self.forward_payload(contact)).status_code, 200
            )
        self.assertEqual(Forward.objects.count(), 0)

    def test_forward_audit_failure_rolls_back_queue_and_contact_version(self):
        contact = self.contact()
        version = contact.updated_at
        with (
            patch("apps.contacts.forwarding.smtp_connection"),
            patch("apps.contacts.forwarding.audit", side_effect=DatabaseError("test audit")),
        ):
            with self.assertRaises(DatabaseError):
                self.client.post(self.url(contact), self.forward_payload(contact))
        contact.refresh_from_db()
        self.assertEqual(contact.updated_at, version)
        self.assertEqual(Forward.objects.count(), 0)

    def test_worker_sends_snapshot_plain_text_from_site_with_stable_message_id(self):
        contact = self.contact()
        job = self.queue(contact)
        self.policy.smtp_from_email = "site@example.test"
        self.policy.save()
        ContactRequest.objects.filter(pk=contact.pk).update(
            message="Modification après mise en file"
        )
        claimed = claim_forward(self.site.pk)
        self.assertIsNone(claim_forward(self.site.pk))
        with patch(
            "apps.contacts.notifications.smtp_connection",
            return_value=mail.get_connection("django.core.mail.backends.locmem.EmailBackend"),
        ):
            self.assertEqual(deliver_forward(claimed), "sent")
            self.assertEqual(deliver_forward(claimed), "cancelled")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [job.recipient])
        self.assertEqual(message.from_email, "site@example.test")
        self.assertEqual(message.body, job.body)
        self.assertEqual(message.content_subtype, "plain")
        self.assertEqual(message.subject, "Demande de contact retransmise")
        self.assertIn(str(job.submission_id), message.extra_headers["Message-ID"])
        job.refresh_from_db()
        self.assertEqual(job.state, "sent")
        self.assertIsNotNone(job.sent_at)

    def test_revocation_before_delivery_cancels_manual_forward(self):
        self.queue()
        job = claim_forward(self.site.pk)
        self.role.permissions.remove(
            Permission.objects.get(
                content_type__app_label="contacts", codename="forward_contactrequest"
            )
        )
        with patch("apps.contacts.notifications.smtp_connection") as smtp:
            self.assertEqual(deliver_forward(job), "cancelled")
        smtp.assert_not_called()

    def test_failure_retries_then_manual_retry_is_versioned(self):
        contact = self.contact()
        job = self.queue(contact)
        for attempt in range(5):
            Forward.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
            with patch(
                "apps.contacts.notifications.smtp_connection", side_effect=OSError("private-secret")
            ):
                state = deliver_forward(claim_forward(self.site.pk))
            self.assertEqual(state, "failed" if attempt == 4 else "pending")
        contact.refresh_from_db()
        self.assertNotContains(self.client.get(self.treatment_url(contact)), "private-secret")
        values = {"version": contact.updated_at.isoformat(), "confirm": "on"}
        url = self.url(contact, "forward-retry", job)
        with patch("apps.contacts.forwarding.smtp_connection"):
            self.assertEqual(self.client.post(url, values).status_code, 302)
            self.assertEqual(self.client.post(url, values).status_code, 409)
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("pending", 0))

    def test_archive_suspends_pending_forward_and_restore_does_not_resend_it(self):
        contact = self.contact()
        job = self.queue(contact)
        contact.refresh_from_db()
        self.assertEqual(
            self.client.post(
                self.url(contact, "archive"),
                {"version": contact.updated_at.isoformat(), "confirm": "on"},
            ).status_code,
            302,
        )
        job.refresh_from_db()
        self.assertEqual(job.state, "cancelled")
        contact.refresh_from_db()
        self.assertEqual(
            self.client.post(
                self.url(contact, "restore"),
                {"version": contact.updated_at.isoformat(), "confirm": "on"},
            ).status_code,
            302,
        )
        job.refresh_from_db()
        self.assertEqual(job.state, "cancelled")

    def test_classification_filter_sort_and_foreign_or_inactive_choices(self):
        contact = self.contact(title="Zèle")
        values = {
            "version": contact.updated_at.isoformat(),
            "status": "accepted",
            "category": self.category.pk,
        }
        self.assertEqual(self.client.post(self.treatment_url(contact), values).status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.category, self.category)
        self.contact(title="Alpha")
        listing = reverse("contacts:list", args=[self.site.pk])
        response = self.client.get(listing, {"order": "title"})
        self.assertEqual([c.title for c in response.context["page"]], ["Alpha", "Zèle"])
        self.assertEqual(
            self.client.get(listing, {"category": self.category.pk})
            .context["page"]
            .paginator.count,
            1,
        )
        self.assertEqual(
            self.client.get(listing, {"category": "none"}).context["page"].paginator.count, 1
        )
        self.assertEqual(self.client.get(listing, {"order": "private_field"}).status_code, 404)
        foreign = LookupList.objects.create(
            site=self.other,
            module="contacts",
            kind="category",
            code="foreign",
            field_key="contacts_contactrequest.category",
            name="Other",
        )
        foreign_category = LookupEntry.objects.create(
            lookup_list=foreign, value="other", label="Other"
        )
        self.assertEqual(
            self.client.post(
                self.treatment_url(contact),
                {
                    **values,
                    "version": contact.updated_at.isoformat(),
                    "category": foreign_category.pk,
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(listing, {"category": foreign_category.pk}).status_code, 404
        )
        self.category.is_active = False
        self.category.save()
        self.assertEqual(
            self.client.post(
                self.treatment_url(contact), {**values, "version": contact.updated_at.isoformat()}
            ).status_code,
            302,
        )
        other_contact = ContactRequest.objects.get(title="Alpha")
        self.assertEqual(
            self.client.post(
                self.treatment_url(other_contact),
                {
                    "version": other_contact.updated_at.isoformat(),
                    "status": "accepted",
                    "category": self.category.pk,
                },
            ).status_code,
            200,
        )

    def test_queue_capacity_shared_with_notifications(self):
        with patch("apps.contacts.forwarding.pending_count", return_value=1000):
            contact = self.contact()
            self.assertContains(
                self.client.post(self.url(contact), self.forward_payload(contact)), "file d’envoi"
            )
        self.assertEqual(Forward.objects.count(), 0)

    def test_public_form_to_classified_inbox_to_smtp_and_trash(self):
        response = reception_tests.ContactsTests.web_post(self)
        self.assertEqual(response.status_code, 202)
        contact = ContactRequest.objects.get(site=self.site)
        self.assertContains(self.client.get(self.treatment_url(contact)), self.payload["message"])
        self.assertEqual(
            self.client.post(
                self.treatment_url(contact),
                {
                    "version": contact.updated_at.isoformat(),
                    "status": "accepted",
                    "category": self.category.pk,
                    "note": "Suivi réservé aux agents",
                },
            ).status_code,
            302,
        )
        contact.refresh_from_db()
        job = self.queue(contact)
        with patch(
            "apps.contacts.notifications.smtp_connection",
            return_value=mail.get_connection("django.core.mail.backends.locmem.EmailBackend"),
        ):
            call_command(
                "process_contact_notifications",
                site_code=self.site.code,
                limit=5,
                stdout=StringIO(),
            )
        self.assertEqual(len(mail.outbox), 2)
        forwarded = next(message for message in mail.outbox if message.to == [job.recipient])
        self.assertIn(self.payload["message"], forwarded.body)
        self.assertNotIn("Suivi réservé aux agents", forwarded.body)
        contact.refresh_from_db()
        self.assertEqual(contact.category, self.category)
        self.assertEqual(
            self.client.post(
                self.url(contact, "archive"),
                {"version": contact.updated_at.isoformat(), "confirm": "on"},
            ).status_code,
            302,
        )
        listing = reverse("contacts:list", args=[self.site.pk])
        self.assertEqual(self.client.get(listing).context["page"].paginator.count, 0)
        self.assertEqual(
            self.client.get(listing, {"state": "trash"}).context["page"].paginator.count, 1
        )

    def test_retry_obeys_shared_capacity_and_missing_category_preserves_classification(self):
        contact = self.contact(category=self.category)
        self.assertEqual(
            self.client.post(
                self.treatment_url(contact),
                {"version": contact.updated_at.isoformat(), "status": "accepted"},
            ).status_code,
            302,
        )
        contact.refresh_from_db()
        self.assertEqual(contact.category, self.category)
        job = self.queue(contact)
        Forward.objects.filter(pk=job.pk).update(state="failed")
        contact.refresh_from_db()
        with patch("apps.contacts.forwarding.pending_count", return_value=1000):
            response = self.client.post(
                self.url(contact, "forward-retry", job),
                {"version": contact.updated_at.isoformat(), "confirm": "on"},
            )
        self.assertContains(response, "file d’envoi")
        job.refresh_from_db()
        self.assertEqual(job.state, "failed")


class ForwardConcurrencyTests(TransactionTestCase):
    def test_two_workers_claim_only_one_forward(self):
        site = Site.objects.create(code="forward-concurrency", name="Test", contacts_enabled=True)
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
        job = Forward.objects.create(
            contact=contact, recipient="recipient@example.test", body="Texte", payload_hash="c" * 64
        )
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                claimed = claim_forward(site.pk)
                return claimed.pk if claimed else None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(results.count(job.pk), 1)
        self.assertEqual(results.count(None), 1)
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)
