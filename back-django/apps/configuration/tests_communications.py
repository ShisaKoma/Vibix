import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.tenancy.models import Role, Site, SiteMembership

from .communications import is_contact_spam, normalize_spam_terms, smtp_connection
from .models import SiteCommunicationSettings


@override_settings(SITE_SMTP_ALLOWED_HOSTS=["smtp.example.test"])
class CommunicationSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="communications-manager")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.site = Site.objects.create(code="communications", name="Communications")
        cls.other_site = Site.objects.create(code="communications-other", name="Autre site")
        cls.role = Role.objects.create(code="communications-manager", name="Gestionnaire")
        cls.role.permissions.add(
            Permission.objects.get(
                content_type__app_label="tenancy", codename="manage_site_settings"
            )
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("configuration:site-settings", args=[self.site.pk])

    def post_section(self, section, **changes):
        values = {
            "smtp": {
                "smtp_host": "smtp.example.test",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "smtp_from_email": "sender@example.test",
                "smtp_username": "",
                "smtp_from_name": "Notre équipe",
            },
            "contact": {
                "contact_recipient": "contacts@example.test",
                "contact_hourly_limit": 100,
                "contact_ip_hourly_limit": 5,
                "contact_max_length": 10000,
                "contact_retention_days": 90,
                "spam_terms": "Crédit facile\nCREDIT FACILE",
                "spam_scan_content": "on",
                "spam_scan_metadata": "on",
                "contact_addressbook_mode": "manual",
            },
        }[section]
        current = SiteCommunicationSettings.objects.filter(site=self.site).first()
        values["version"] = current.updated_at.isoformat() if current else "new"
        values.update(changes)
        return self.client.post(
            self.url,
            {
                "settings_section": section,
                **{f"{section}-{key}": value for key, value in values.items()},
            },
        )

    def test_get_does_not_create_settings_and_preparatory_state_is_visible(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Messagerie SMTP")
        self.assertContains(response, "Réception des contacts")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(SiteCommunicationSettings.objects.exists())

    def test_settings_saved_per_site_and_audited_without_values(self):
        self.assertEqual(self.post_section("contact").status_code, 302)
        policy = SiteCommunicationSettings.objects.get(site=self.site)
        self.assertEqual(policy.spam_terms, "credit facile")
        self.assertFalse(SiteCommunicationSettings.objects.filter(site=self.other_site).exists())
        event = AuditEvent.objects.get(action="settings.site.contact.update")
        self.assertEqual(event.site_id, self.site.pk)
        self.assertEqual(event.entity_id, str(policy.pk))

    def test_smtp_and_contact_sections_preserve_each_other(self):
        self.post_section("contact")
        self.assertEqual(self.post_section("smtp").status_code, 302)
        policy = SiteCommunicationSettings.objects.get(site=self.site)
        self.assertEqual(policy.contact_recipient, "contacts@example.test")
        self.assertEqual(policy.spam_terms, "credit facile")
        self.assertEqual(policy.smtp_host, "smtp.example.test")

    def test_other_site_and_revoked_permission_are_denied(self):
        other_url = reverse("configuration:site-settings", args=[self.other_site.pk])
        self.assertEqual(self.client.get(other_url).status_code, 404)
        self.assertEqual(self.client.post(other_url, {"settings_section": "smtp"}).status_code, 404)
        self.role.permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.post_section("contact").status_code, 404)

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {"settings_section": "contact"}).status_code, 403)

    def test_stale_version_conflicts_without_overwriting(self):
        self.post_section("contact")
        response = self.post_section("contact", version="new", spam_terms="nouveau")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            SiteCommunicationSettings.objects.get(site=self.site).spam_terms, "credit facile"
        )
        self.assertContains(response, "Rechargez", status_code=409)

    def test_audit_failure_rolls_back_write(self):
        with patch("apps.configuration.views.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.post_section("contact")
        self.assertFalse(SiteCommunicationSettings.objects.filter(site=self.site).exists())

    def test_invalid_quotas_and_disabled_scan_rejected(self):
        for changes in (
            {"contact_hourly_limit": 0},
            {"contact_ip_hourly_limit": 101},
            {"contact_hourly_limit": 3, "contact_ip_hourly_limit": 5},
            {"contact_max_length": 50001},
            {"spam_scan_content": "", "spam_scan_metadata": ""},
        ):
            with self.subTest(changes=changes):
                response = self.post_section("contact", **changes)
                self.assertTrue(response.context["contact_form"].errors)
        self.assertFalse(SiteCommunicationSettings.objects.exists())

    def test_sync_requires_active_addressbook(self):
        self.site.addressbook_enabled = False
        self.site.save()
        response = self.post_section("contact", contact_addressbook_mode="sync")
        self.assertIn("contact_addressbook_mode", response.context["contact_form"].errors)

    def test_smtp_requires_approved_host_and_secret(self):
        for changes in (
            {"smtp_enabled": "on", "smtp_host": "localhost"},
            {"smtp_enabled": "on", "smtp_username": "user"},
            {"smtp_security": "plain"},
            {"smtp_port": 65536},
            {"smtp_from_name": "a\r\nBcc: injected@example.test"},
        ):
            with self.subTest(changes=changes):
                with patch.dict(os.environ, {}, clear=True):
                    response = self.post_section("smtp", **changes)
                self.assertTrue(response.context["smtp_form"].errors)
        self.assertFalse(SiteCommunicationSettings.objects.exists())

    def test_secret_never_rendered_or_stored_and_smtp_never_opened_on_save(self):
        key = f"SITE_SMTP_PASSWORD_{self.site.pk}"
        with patch.dict(os.environ, {key: "smtp-test-only-secret"}, clear=True):
            with patch("apps.configuration.communications.EmailBackend.open") as opened:
                self.assertEqual(
                    self.post_section("smtp", smtp_enabled="on", smtp_username="user").status_code,
                    302,
                )
                opened.assert_not_called()
            response = self.client.get(self.url)
            self.assertNotContains(response, "smtp-test-only-secret")
            configuration = SiteCommunicationSettings.objects.get(site=self.site)
            connection = smtp_connection(configuration)
            self.assertEqual(connection.password, "smtp-test-only-secret")
            self.assertTrue(connection.use_tls)
            self.assertFalse(connection.use_ssl)
            self.assertEqual(connection.timeout, 10)
            self.assertNotIn("smtp-test-only-secret", str(configuration.__dict__))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                smtp_connection(configuration)
        with override_settings(SITE_SMTP_ALLOWED_HOSTS=[]):
            with self.assertRaises(ValidationError):
                smtp_connection(configuration)

    def test_private_configuration_not_exposed_by_public_api(self):
        self.post_section("contact")
        response = self.client.get(reverse("api-public-site", args=[self.site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "contacts@example.test")
        self.assertNotContains(response, "credit facile")

    def test_spam_matches_metadata_or_content_with_accents_and_case(self):
        policy = SiteCommunicationSettings(site=self.site, spam_terms="crédit facile")
        self.assertTrue(is_contact_spam(policy, message="Obtenez un CRÉDIT FACILE !"))
        self.assertTrue(is_contact_spam(policy, title="credit facile"))
        self.assertTrue(is_contact_spam(policy, organization="CRÉDIT FACILE"))
        self.assertFalse(is_contact_spam(policy, message="Demande de renseignements"))
        policy.spam_scan_metadata = False
        self.assertFalse(is_contact_spam(policy, title="credit facile"))
        policy.spam_scan_content = False
        policy.spam_scan_metadata = True
        self.assertFalse(is_contact_spam(policy, message="credit facile"))

    def test_spam_rules_are_literal_and_bounded(self):
        policy = SiteCommunicationSettings(site=self.site, spam_terms=".*")
        self.assertFalse(is_contact_spam(policy, message="Message normal"))
        self.assertTrue(is_contact_spam(policy, title=".*"))
        for value in ("x" * 101, "\n".join(f"term-{i}" for i in range(201))):
            with self.assertRaises(ValidationError):
                normalize_spam_terms(value)
        with self.assertRaises(ValidationError):
            is_contact_spam(policy, message="x" * 50001)

    def test_database_rejects_ip_quota_above_site_quota(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteCommunicationSettings.objects.create(
                site=self.site, contact_hourly_limit=2, contact_ip_hourly_limit=5
            )
