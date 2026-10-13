import json
import time
import uuid
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.tenancy.models import Site

from . import tests as reception_tests
from .definition_forms import DefinitionForm
from .forwarding import forward_body
from .models import ContactFormDefinition, ContactRequest
from .seeds import seed_contact_forms


class FormDefinitionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reception_tests.ContactsTests.setUpTestData.__func__(cls)
        seed_contact_forms(cls.site)
        cls.role.permissions.add(
            Permission.objects.get(
                content_type__app_label="tenancy", codename="manage_site_settings"
            )
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.browser = Client(enforce_csrf_checks=True)
        self.definition = ContactFormDefinition.objects.get(site=self.site, code="devis")
        self.payload = {
            "name": "Exemple",
            "first_name": "Camille",
            "email": "camille@example.test",
            "phone": "",
            "organization": "",
            "title": "Projet photo",
            "message": "Bonjour",
        }
        self.now = time.time()

    def configuration(self, **changes):
        return {
            "code": self.definition.code,
            "label": self.definition.label,
            "is_active": "on",
            "position": self.definition.position,
            "version": self.definition.updated_at.isoformat(),
            "question_01_active": "on",
            "question_01_label": "Lieu de la séance",
            "question_01_required": "on",
            **changes,
        }

    def configure(self, **changes):
        form = DefinitionForm(self.configuration(**changes), instance=self.definition)
        self.assertTrue(form.is_valid(), form.errors)
        self.definition = form.save()

    def prepare(self):
        with patch("apps.contacts.security.time.time", return_value=self.now):
            self.challenge = self.browser.get(
                reverse("contacts:challenge", args=[self.site.pk]), {"type": "devis"}
            ).json()
        self.submission = {
            **self.payload,
            "form_type": "devis",
            "form_version": self.challenge["form"]["version"],
            "question_01": "Studio",
            "challenge": self.challenge["challenge"],
        }

    def submit(self, **changes):
        with patch("apps.contacts.security.time.time", return_value=self.now + 3):
            return self.browser.post(
                reverse("contacts:submit", args=[self.site.pk]),
                json.dumps({**self.submission, **changes}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=self.challenge["csrf_token"],
            )

    def test_configuration_permissions_isolation_version_and_audit(self):
        edit_url = reverse("contacts:definition-edit", args=[self.site.pk, self.definition.pk])
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("contacts:definition-edit", args=[self.other.pk, self.definition.pk])
            ).status_code,
            404,
        )
        values = self.configuration()
        self.assertEqual(self.client.post(edit_url, values).status_code, 302)
        self.assertTrue(
            AuditEvent.objects.filter(
                site=self.site,
                action="settings.contacts.form.update",
                entity_id=str(self.definition.pk),
            ).exists()
        )
        self.assertEqual(self.client.post(edit_url, values).status_code, 409)
        self.definition.refresh_from_db()
        self.assertEqual(self.definition.questions[0]["label"], "Lieu de la séance")
        self.role.permissions.remove(
            Permission.objects.get(
                content_type__app_label="tenancy", codename="manage_site_settings"
            )
        )
        self.assertEqual(self.client.get(edit_url).status_code, 404)

    def test_required_question_and_disabled_fields_are_validated(self):
        self.configure()
        self.prepare()
        self.assertEqual(self.submit(question_01="").status_code, 400)
        self.assertEqual(self.submit(question_02="Champ désactivé").status_code, 400)
        self.assertEqual(self.submit(form_version="autre-version").status_code, 400)
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())
        self.assertEqual(self.submit().status_code, 202)

    def test_receipt_snapshot_survives_renaming_deactivation_and_exact_retry(self):
        self.configure()
        self.prepare()
        self.assertEqual(self.submit().status_code, 202)
        contact = ContactRequest.objects.get(site=self.site)
        self.configure(label="Nouveau devis", question_01_label="Autre question", is_active="")
        self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(self.submit(question_01="Autre réponse").status_code, 409)
        self.assertEqual(ContactRequest.objects.filter(site=self.site).count(), 1)
        contact.refresh_from_db()
        self.assertEqual(contact.form_snapshot["label"], "Demande de devis")
        self.assertEqual(contact.form_snapshot["questions"][0]["label"], "Lieu de la séance")
        self.assertEqual(contact.answers, {"question_01": "Studio"})
        self.assertIn("Lieu de la séance\nStudio", forward_body(contact))
        self.assertContains(
            self.client.get(reverse("contacts:detail", args=[self.site.pk, contact.pk])),
            "Lieu de la séance",
        )
        self.assertEqual(
            self.browser.get(
                reverse("contacts:challenge", args=[self.site.pk]), {"type": "devis"}
            ).status_code,
            404,
        )

    def test_stale_first_submission_cannot_use_new_labels(self):
        self.configure()
        self.prepare()
        self.configure(question_01_label="Nouvelle signification")
        self.assertEqual(self.submit().status_code, 400)
        self.assertFalse(ContactRequest.objects.filter(site=self.site).exists())

    def test_server_client_replays_with_snapshot_after_configuration_changes(self):
        self.configure()
        key = str(uuid.uuid4())
        data = {
            **self.payload,
            "form_type": "devis",
            "form_version": self.definition.updated_at.isoformat(),
            "question_01": "Studio",
        }

        def post(values):
            return self.browser.post(
                reverse("contacts:submit", args=[self.site.pk]),
                json.dumps(values),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.api_token}",
                HTTP_IDEMPOTENCY_KEY=key,
            )

        self.assertEqual(post(data).status_code, 202)
        self.configure(question_01_active="", is_active="")
        self.assertEqual(post(data).status_code, 202)
        self.assertEqual(post({**data, "question_01": "Autre lieu"}).status_code, 409)

    def test_html_form_uses_same_schema_and_validates_questions(self):
        self.configure()
        with patch("apps.contacts.security.time.time", return_value=self.now):
            response = self.browser.get(
                reverse("contacts:public-form", args=[self.site.pk]), {"type": "devis"}
            )
        self.assertContains(response, "Lieu de la séance")
        form = response.context["form"]
        data = {
            **self.payload,
            "form_type": "devis",
            "form_version": self.definition.updated_at.isoformat(),
            "question_01": "Studio",
            "challenge": form.initial["challenge"],
            "csrfmiddlewaretoken": self.browser.cookies["csrftoken"].value,
        }
        with patch("apps.contacts.security.time.time", return_value=self.now + 3):
            self.assertEqual(
                self.browser.post(
                    reverse("contacts:public-form", args=[self.site.pk]),
                    urlencode(data),
                    content_type="application/x-www-form-urlencoded",
                ).status_code,
                202,
            )
        self.assertEqual(
            ContactRequest.objects.get(site=self.site).answers["question_01"], "Studio"
        )

    def test_seeding_preserves_customizations_and_excludes_owner(self):
        self.configure(label="Sur mesure", is_active="")
        count = AuditEvent.objects.filter(
            site=self.site, action="setup.contacts.form.create"
        ).count()
        self.assertEqual(seed_contact_forms(self.site), 0)
        self.definition.refresh_from_db()
        self.assertEqual(self.definition.label, "Sur mesure")
        self.assertFalse(self.definition.is_active)
        self.assertEqual(
            AuditEvent.objects.filter(site=self.site, action="setup.contacts.form.create").count(),
            count,
        )
        self.assertEqual(seed_contact_forms(Site.objects.get(pk=1)), 0)
        self.assertFalse(ContactFormDefinition.objects.filter(site_id=1).exists())
