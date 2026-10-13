from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.tenancy.models import Role, Site, SiteMembership

from .models import UserProfile

User = get_user_model()


class FirstRunTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="new-user", password="A-real-test-secret-6742")
        cls.site = Site.objects.create(code="new-site", name="Site du nouvel utilisateur")
        cls.other_site = Site.objects.create(code="other-site", name="Autre site")
        cls.role = Role.objects.create(code="manager", name="Administrateur de site")
        cls.role.permissions.add(
            Permission.objects.get(content_type__app_label="tenancy", codename="view_site"),
            Permission.objects.get(
                content_type__app_label="tenancy", codename="manage_site_settings"
            ),
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)

    def setUp(self):
        self.client.force_login(self.user)

    def test_first_open_redirects_to_wizard_and_site_settings(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("first-run"), fetch_redirect_response=False)

        response = self.client.get(reverse("first-run"))
        self.assertContains(response, "Préparons votre espace")
        self.assertContains(response, reverse("configuration:site-settings", args=[self.site.pk]))
        self.assertNotContains(
            response, reverse("configuration:site-settings", args=[self.other_site.pk])
        )

    def test_first_run_gate_covers_admin_and_configuration(self):
        for url in (
            reverse("admin:index"),
            reverse("configuration:site-settings", args=[self.site.pk]),
        ):
            with self.subTest(url=url):
                self.assertRedirects(
                    self.client.get(url), reverse("first-run"), fetch_redirect_response=False
                )

    def test_api_before_first_run_still_enforces_site_permissions(self):
        self.assertEqual(self.client.get(reverse("api-sites")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("api-site-detail", args=[self.site.pk])).status_code, 200
        )
        self.role.permissions.clear()
        self.assertEqual(
            self.client.get(reverse("api-site-detail", args=[self.site.pk])).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("api-site-detail", args=[self.other_site.pk])).status_code, 404
        )

    def test_completion_records_preferred_site_and_audit_then_allows_dashboard(self):
        response = self.client.post(reverse("first-run"), {"preferred_site": self.site.pk})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.preferred_site, self.site)
        self.assertIsNotNone(profile.first_run_completed_at)
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.user,
                site=self.site,
                action="auth.first_run.completed",
            ).exists()
        )
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_cannot_choose_a_site_outside_user_access(self):
        response = self.client.post(reverse("first-run"), {"preferred_site": self.other_site.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sélectionnez un choix valide")
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())

    def test_user_without_a_site_can_only_logout_until_assigned(self):
        user = User.objects.create_user(username="unassigned", password="A-real-test-secret-6742")
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("first-run"), fetch_redirect_response=False)
        self.assertContains(
            self.client.get(reverse("first-run")), "Aucun site n’est encore attribué"
        )
        self.assertEqual(self.client.post(reverse("first-run"), {}).status_code, 200)
        self.assertFalse(UserProfile.objects.filter(user=user).exists())
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)

    def test_preferred_site_is_shown_first_on_dashboard(self):
        second_site = Site.objects.create(code="second-site", name="Second site")
        SiteMembership.objects.create(user=self.user, site=second_site, role=self.role)
        UserProfile.objects.create(
            user=self.user, preferred_site=second_site, first_run_completed_at=timezone.now()
        )
        response = self.client.get(reverse("dashboard"))
        self.assertLess(
            response.content.index(b"Second site"),
            response.content.index(b"Site du nouvel utilisateur"),
        )
