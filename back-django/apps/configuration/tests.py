from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.tenancy.models import Role, Site, SiteMembership

from .models import LookupEntry, LookupList

User = get_user_model()


class SiteConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="manager", password="A-real-test-secret-6742")
        cls.site = Site.objects.create(code="manager-site", name="Site géré")
        cls.other_site = Site.objects.create(code="other-site", name="Autre site")
        cls.role = Role.objects.create(code="site-manager", name="Administrateur de site")
        cls.permission = Permission.objects.get(
            content_type__app_label="tenancy", codename="manage_site_settings"
        )
        cls.role.permissions.add(cls.permission)
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.user)

    def branding_data(self, **changes):
        return {
            "description": "Le site de notre équipe.",
            "primary_color_1": "#abcdef",
            "primary_color_2": "#112233",
            "secondary_color_1": "#445566",
            "secondary_color_2": "#778899",
            **changes,
        }

    def icon_upload(self):
        output = BytesIO()
        Image.new("RGBA", (500, 300), "blue").save(output, "PNG")
        return SimpleUploadedFile("icon.png", output.getvalue(), content_type="image/png")

    def isolated_media(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=folder))

    def test_site_branding_saved_audited_and_exposed_in_api(self):
        self.isolated_media()
        url = reverse("configuration:site-settings", args=[self.site.pk])
        response = self.client.post(
            url, self.branding_data(icon=self.icon_upload(), code="ignored")
        )
        self.assertRedirects(response, url)
        self.site.refresh_from_db()
        self.assertEqual(self.site.description, "Le site de notre équipe.")
        self.assertEqual(self.site.primary_color_1, "#ABCDEF")
        self.assertEqual(self.site.code, "manager-site")
        with Image.open(self.site.icon.path) as icon:
            self.assertEqual(icon.size, (256, 154))
            self.assertEqual(icon.format, "PNG")
        self.assertTrue(
            AuditEvent.objects.filter(site=self.site, action="settings.site.update").exists()
        )
        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="tenancy", codename="view_site")
        )
        listing = self.client.get(reverse("api-sites")).json()["results"][0]
        detail = self.client.get(reverse("api-site-detail", args=[self.site.pk])).json()
        self.assertEqual(listing, detail)
        self.assertEqual(detail["description"], self.site.description)
        self.assertEqual(
            detail["colors"],
            {"primary": ["#ABCDEF", "#112233"], "secondary": ["#445566", "#778899"]},
        )
        self.assertEqual(
            detail["icon_url"], "http://testserver" + reverse("site-icon", args=[self.site.pk])
        )
        response = self.client.get(reverse("site-icon", args=[self.site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))
        self.assertContains(self.client.get(reverse("dashboard")), self.site.description)

    def test_branding_rejects_invalid_color_and_image(self):
        url = reverse("configuration:site-settings", args=[self.site.pk])
        response = self.client.post(url, self.branding_data(primary_color_1="red"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("primary_color_1", response.context["form"].errors)
        invalid = SimpleUploadedFile("icon.png", b"not an image", content_type="image/png")
        response = self.client.post(url, self.branding_data(icon=invalid))
        self.assertIn("icon", response.context["form"].errors)
        oversized = SimpleUploadedFile(
            "big.png", b"x" * (2 * 1024 * 1024 + 1), content_type="image/png"
        )
        response = self.client.post(url, self.branding_data(icon=oversized))
        self.assertIn("icon", response.context["form"].errors)
        self.site.refresh_from_db()
        self.assertEqual(self.site.description, "")
        self.assertFalse(self.site.icon)

    def test_site_icon_preserved_then_removed(self):
        self.isolated_media()
        url = reverse("configuration:site-settings", args=[self.site.pk])
        self.client.post(url, self.branding_data(icon=self.icon_upload()))
        self.site.refresh_from_db()
        original = self.site.icon.name
        self.client.post(url, self.branding_data(description="Description modifiée"))
        self.site.refresh_from_db()
        self.assertEqual(self.site.icon.name, original)
        response = self.client.get(url)
        self.assertContains(response, "icon-clear")
        self.assertNotContains(response, self.site.icon.url)
        self.client.post(url, self.branding_data(**{"icon-clear": "on"}))
        self.site.refresh_from_db()
        self.assertFalse(self.site.icon)
        self.assertIsNone(self.client.get(reverse("api-sites")).json()["results"][0]["icon_url"])
        self.assertEqual(
            self.client.get(reverse("site-icon", args=[self.site.pk])).status_code, 404
        )

    def test_branding_and_icon_are_isolated_by_site_and_csrf(self):
        url = reverse("configuration:site-settings", args=[self.other_site.pk])
        self.assertEqual(self.client.post(url, self.branding_data()).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("site-icon", args=[self.other_site.pk])).status_code, 404
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        url = reverse("configuration:site-settings", args=[self.site.pk])
        self.assertEqual(csrf_client.post(url, self.branding_data()).status_code, 403)
        self.role.permissions.clear()
        self.assertEqual(self.client.post(url, self.branding_data()).status_code, 404)

    def test_branding_rolls_back_if_audit_fails(self):
        self.isolated_media()
        url = reverse("configuration:site-settings", args=[self.site.pk])
        with patch("apps.configuration.views.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.post(url, self.branding_data(icon=self.icon_upload()))
        self.site.refresh_from_db()
        self.assertFalse(self.site.icon)
        self.assertEqual(self.site.description, "")
        from pathlib import Path

        from django.conf import settings

        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.png")), [])

    def test_dashboard_offers_configuration_only_for_managed_site(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("configuration:site-settings", args=[self.site.pk]))
        self.assertNotContains(
            response, reverse("configuration:site-settings", args=[self.other_site.pk])
        )

    def test_settings_are_hidden_without_site_membership_or_management_permission(self):
        self.assertEqual(
            self.client.get(
                reverse("configuration:site-settings", args=[self.other_site.pk])
            ).status_code,
            404,
        )
        self.role.permissions.clear()
        self.assertEqual(
            self.client.get(
                reverse("configuration:site-settings", args=[self.site.pk])
            ).status_code,
            404,
        )

    def test_admin_creates_and_updates_category_and_dropdown_values(self):
        category_url = reverse("configuration:lookup-lists", args=[self.site.pk, "category"])
        response = self.client.post(
            category_url,
            {
                "module": "cobweb",
                "field_key": "cobweb_item.category",
                "code": "product-category",
                "name": "Catégories produits",
                "is_active": "on",
            },
        )
        item = LookupList.objects.get(site=self.site, module="cobweb", kind="category")
        self.assertRedirects(
            response, reverse("configuration:lookup-edit", args=[self.site.pk, item.pk])
        )
        edit_url = reverse("configuration:lookup-edit", args=[self.site.pk, item.pk])
        response = self.client.post(
            edit_url,
            {
                "module": "cobweb",
                "field_key": "cobweb_item.category",
                "code": "product-category",
                "name": "Catégories produits",
                "is_active": "on",
                "entries-TOTAL_FORMS": "1",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "0",
                "entries-MAX_NUM_FORMS": "200",
                "entries-0-value": "office",
                "entries-0-label": "Bureau",
                "entries-0-position": "0",
                "entries-0-is_active": "on",
                "entries-0-parent": "",
            },
        )
        self.assertRedirects(response, edit_url)
        entry = LookupEntry.objects.get(lookup_list=item, value="office")
        self.assertEqual(entry.label, "Bureau")
        self.assertTrue(
            AuditEvent.objects.filter(action="settings.reference.create", site=self.site).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="settings.reference.update", site=self.site).exists()
        )

    def test_lists_are_separated_by_site_and_module(self):
        item = LookupList.objects.create(
            site=self.site, module="products", kind="category", code="category", name="Produits"
        )
        other_module = LookupList.objects.create(
            site=self.site,
            module="directories",
            kind="category",
            code="category",
            name="Répertoires",
        )
        other_site_list = LookupList.objects.create(
            site=self.other_site,
            module="products",
            kind="category",
            code="category",
            name="Autres produits",
        )
        self.assertEqual(
            {item.pk, other_module.pk, other_site_list.pk},
            set(LookupList.objects.values_list("pk", flat=True)),
        )

    def test_cross_site_list_edit_is_not_found(self):
        item = LookupList.objects.create(
            site=self.other_site,
            module="products",
            kind="category",
            code="category",
            name="Private",
        )
        response = self.client.get(
            reverse("configuration:lookup-edit", args=[self.site.pk, item.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_duplicate_list_code_shows_validation_error(self):
        LookupList.objects.create(
            site=self.site,
            module="cobweb",
            kind="category",
            code="category",
            name="Cobweb",
            field_key="cobweb_item.category",
        )
        response = self.client.post(
            reverse("configuration:lookup-lists", args=[self.site.pk, "category"]),
            {
                "module": "cobweb",
                "field_key": "cobweb_item.category",
                "code": "category",
                "name": "Doublon",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "existe déjà")
        self.assertEqual(LookupList.objects.filter(site=self.site, module="cobweb").count(), 1)

    def test_category_parent_stays_in_same_list_and_cannot_form_a_cycle(self):
        item = LookupList.objects.create(
            site=self.site, module="products", kind="category", code="category", name="Produits"
        )
        root = LookupEntry.objects.create(lookup_list=item, value="root", label="Parent")
        child = LookupEntry.objects.create(
            lookup_list=item, parent=root, value="child", label="Enfant"
        )
        root.parent = child
        with self.assertRaises(ValidationError):
            root.full_clean()

    def test_dropdown_values_cannot_be_hierarchical(self):
        item = LookupList.objects.create(
            site=self.site, module="products", kind="choice", code="status", name="Statut"
        )
        parent = LookupEntry.objects.create(lookup_list=item, value="draft", label="Brouillon")
        child = LookupEntry(lookup_list=item, parent=parent, value="review", label="En revue")
        with self.assertRaises(ValidationError):
            child.full_clean()

    def test_reader_cannot_open_site_settings(self):
        self.role.permissions.clear()
        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="tenancy", codename="view_site")
        )
        self.assertEqual(
            self.client.get(
                reverse("configuration:site-settings", args=[self.site.pk])
            ).status_code,
            404,
        )
