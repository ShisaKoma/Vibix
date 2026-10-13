from unittest.mock import patch

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
from apps.tenancy.permissions import has_site_permission

User = get_user_model()


class SiteAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="alice", password="A-real-test-secret-6742")
        cls.site = Site.objects.create(code="site-a", name="Site A")
        cls.other_site = Site.objects.create(code="site-b", name="Site B")
        cls.role = Role.objects.create(code="reader", name="Lecteur")
        cls.permission = Permission.objects.get(
            content_type__app_label="tenancy", codename="view_site"
        )
        cls.role.permissions.add(cls.permission)
        cls.membership = SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())

    def setUp(self):
        self.client.force_login(self.user)

    def test_scoped_listing_and_direct_access(self):
        response = self.client.get(reverse("api-sites"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s["id"] for s in response.json()["results"]], [self.site.pk])
        self.assertEqual(
            self.client.get(reverse("api-site-detail", args=[self.site.pk])).status_code, 200
        )
        hidden = self.client.get(reverse("api-site-detail", args=[self.other_site.pk]))
        missing = self.client.get(reverse("api-site-detail", args=[999999]))
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json(), missing.json())
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "Site A")
        self.assertNotContains(dashboard, "Site B")

    def test_workspace_counts_only_accessible_site_content(self):
        from apps.media.models import Asset

        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="media", codename="view_asset")
        )
        for site in (self.site, self.other_site):
            Asset.objects.create(
                site=site,
                title="Image",
                alt_text="Image",
                image="test.png",
                width=1,
                height=1,
                byte_size=1,
            )
        response = self.client.get(reverse("site-workspace", args=[self.site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["module_counts"], {"media": 1})
        self.assertContains(response, "1 média")
        self.assertNotContains(response, f'href="{reverse("cobweb:list", args=[self.site.pk])}"')
        self.role.permissions.clear()
        response = self.client.get(reverse("site-workspace", args=[self.site.pk]))
        self.assertEqual(response.context["module_counts"], {})
        self.assertContains(response, "Aucun module accessible")

    def test_workspace_cobweb_group_access_without_entry_access(self):
        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="cobweb", codename="view_cobwebgroup")
        )
        response = self.client.get(reverse("site-workspace", args=[self.site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["module_counts"]["groups"], 0)
        self.assertNotIn("cobweb", response.context["module_counts"])
        self.assertContains(response, reverse("cobweb:group-list", args=[self.site.pk]))
        self.assertNotContains(response, f'href="{reverse("cobweb:list", args=[self.site.pk])}"')
        self.assertNotContains(response, "Votre rôle ne donne pas encore accès à Cobweb")

    def test_role_permission_revocation_applies_to_existing_session(self):
        self.assertTrue(has_site_permission(self.user, self.site, "tenancy.view_site"))
        self.role.permissions.clear()
        self.assertFalse(has_site_permission(self.user, self.site, "tenancy.view_site"))
        response = self.client.get(reverse("api-site-detail", args=[self.site.pk]))
        self.assertEqual(response.status_code, 403)

    def test_membership_revocation_applies_to_existing_session(self):
        self.membership.is_active = False
        self.membership.save()
        self.assertEqual(self.client.get(reverse("api-sites")).json()["results"], [])
        self.assertEqual(
            self.client.get(reverse("api-site-detail", args=[self.site.pk])).status_code, 404
        )

    def test_disabled_site_is_hidden_even_with_stale_instance(self):
        Site.objects.filter(pk=self.site.pk).update(is_active=False)
        self.assertFalse(has_site_permission(self.user, self.site, "tenancy.view_site"))
        self.assertEqual(self.client.get(reverse("api-sites")).json()["results"], [])

    def test_disabled_account_loses_session_access(self):
        self.user.is_active = False
        self.user.save()
        self.assertFalse(has_site_permission(self.user, self.site, "tenancy.view_site"))
        self.assertEqual(self.client.get(reverse("api-sites")).status_code, 401)

    def test_global_django_permission_does_not_grant_site_permission(self):
        self.user.user_permissions.add(self.permission)
        self.role.permissions.clear()
        self.assertTrue(self.user.has_perm("tenancy.view_site"))
        self.assertFalse(has_site_permission(self.user, self.site, "tenancy.view_site"))
        self.assertFalse(has_site_permission(self.user, self.other_site, "tenancy.view_site"))

    def test_duplicate_membership_rejected_by_postgres(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteMembership.objects.create(user=self.user, site=self.site, role=self.role)

    def test_staff_cannot_enter_global_admin(self):
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:tenancy_site_changelist")).status_code, 302)

    def test_superuser_can_read_all_active_sites(self):
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(self.client.get(reverse("api-sites")).json()["count"], 3)

    def test_read_only_endpoint_rejects_write(self):
        response = self.client.post(
            reverse("api-site-detail", args=[self.site.pk]), {"name": "Changed"}
        )
        self.assertEqual(response.status_code, 405)
        self.site.refresh_from_db()
        self.assertEqual(self.site.name, "Site A")

    def test_list_has_stable_pagination(self):
        self.user.is_superuser = True
        self.user.save()
        Site.objects.bulk_create([Site(code=f"site-{i}", name=f"Site {i:03}") for i in range(60)])
        first = self.client.get(reverse("api-sites")).json()
        second = self.client.get(reverse("api-sites"), {"page": 2}).json()
        self.assertEqual(first["count"], 63)
        self.assertEqual(len(first["results"]), 50)
        self.assertEqual(len(second["results"]), 13)
        self.assertFalse({s["id"] for s in first["results"]} & {s["id"] for s in second["results"]})


class AuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "A-real-test-secret-6742"
        cls.user = User.objects.create_user(username="alice", password=cls.password)

    def test_anonymous_access_requires_authentication(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
        self.assertEqual(self.client.get(reverse("api-sites")).status_code, 401)
        self.assertEqual(self.client.get(reverse("api-site-detail", args=[1])).status_code, 401)

    def test_login_and_logout_are_audited_without_credentials(self):
        response = self.client.post(
            reverse("login"), {"username": "alice", "password": self.password}
        )
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertRedirects(
            self.client.get(reverse("dashboard")),
            reverse("first-run"),
            fetch_redirect_response=False,
        )
        self.assertTrue(AuditEvent.objects.filter(action="auth.login", actor=self.user).exists())
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertTrue(AuditEvent.objects.filter(action="auth.logout", actor=self.user).exists())
        self.assertEqual(self.client.get(reverse("api-sites")).status_code, 401)
        for event in AuditEvent.objects.values():
            self.assertNotIn(self.password, str(event))

    def test_csrf_is_required_for_login(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("login"), {"username": "alice", "password": self.password})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", client.session)

    def test_external_next_url_is_ignored(self):
        response = self.client.post(
            reverse("login"),
            {"username": "alice", "password": self.password, "next": "https://attacker.example/"},
        )
        self.assertEqual(response.headers["Location"], reverse("dashboard"))

    def test_lockout_blocks_correct_password_after_five_failures(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "alice", "password": "wrong"})
        response = self.client.post(
            reverse("login"), {"username": "alice", "password": self.password}
        )
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(AuditEvent.objects.filter(action="auth.login_failed").count(), 6)


class OperationsTests(TestCase):
    def test_health_and_database_failure(self):
        self.assertEqual(self.client.get(reverse("health-live")).status_code, 200)
        self.assertEqual(self.client.get(reverse("health-ready")).status_code, 200)
        with patch("apps.core.views.connection.cursor", side_effect=DatabaseError("private error")):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "private error", status_code=503)

    def test_admin_changes_are_audited_and_audit_is_read_only(self):
        root = User.objects.create_superuser(username="root", password="A-real-test-secret-6742")
        UserProfile.objects.create(user=root, first_run_completed_at=timezone.now())
        self.client.force_login(root)
        response = self.client.post(
            reverse("admin:tenancy_site_add"),
            {
                "code": "new",
                "name": "New",
                "is_active": "on",
                "_save": "1",
                "primary_color_1": "#175EC5",
                "primary_color_2": "#172B43",
                "secondary_color_1": "#E6EDFC",
                "secondary_color_2": "#F3F5F8",
            },
        )
        self.assertEqual(response.status_code, 302)
        event = AuditEvent.objects.get(action="admin.create", entity_type="tenancy.site")
        self.assertEqual(event.actor, root)
        self.assertEqual(event.entity_id, str(Site.objects.get(code="new").pk))
        self.assertEqual(
            self.client.post(
                reverse("admin:audit_auditevent_delete", args=[event.pk]), {"post": "yes"}
            ).status_code,
            403,
        )
        self.assertTrue(AuditEvent.objects.filter(pk=event.pk).exists())

    def test_role_permission_changes_are_audited(self):
        root = User.objects.create_superuser(username="root", password="A-real-test-secret-6742")
        UserProfile.objects.create(user=root, first_run_completed_at=timezone.now())
        role = Role.objects.create(code="reader", name="Lecteur")
        permission = Permission.objects.get(content_type__app_label="tenancy", codename="view_site")
        self.client.force_login(root)
        response = self.client.post(
            reverse("admin:tenancy_role_change", args=[role.pk]),
            {"code": "reader", "name": "Lecteur", "permissions": [permission.pk], "_save": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(role.permissions.filter(pk=permission.pk).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action="admin.update",
                entity_type="tenancy.role",
                entity_id=str(role.pk),
                actor=root,
            ).exists()
        )


class OwnerSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_site = Site.objects.get(pk=1)
        cls.demo = User.objects.create_user(username="demo", password="A-real-test-secret-6742")
        UserProfile.objects.create(user=cls.demo, first_run_completed_at=timezone.now())
        cls.role = Role.objects.create(code="reader", name="Lecteur")
        cls.role.permissions.add(
            Permission.objects.get(content_type__app_label="tenancy", codename="view_site")
        )

    def test_first_site_is_reserved_and_sequence_is_advanced(self):
        self.assertEqual(self.owner_site.code, "owner")
        site = Site.objects.create(code="demo", name="Site de démonstration")
        self.assertGreater(site.pk, 1)

    def test_cannot_assign_owner_site_to_regular_user(self):
        with self.assertRaises(ValidationError):
            SiteMembership.objects.create(user=self.demo, site=self.owner_site, role=self.role)

    def test_bad_membership_cannot_leak_owner_site(self):
        # bulk_create bypasses save/clean: server reads must still deny access.
        SiteMembership.objects.bulk_create(
            [SiteMembership(user=self.demo, site=self.owner_site, role=self.role)]
        )
        self.client.force_login(self.demo)
        self.assertEqual(self.client.get(reverse("api-sites")).json()["results"], [])
        self.assertFalse(has_site_permission(self.demo, self.owner_site, "tenancy.view_site"))
        self.assertEqual(self.client.get(reverse("api-site-detail", args=[1])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("dashboard")), self.owner_site.name)

    def test_staff_is_not_owner(self):
        self.demo.is_staff = True
        self.demo.save()
        self.assertFalse(has_site_permission(self.demo, self.owner_site, "tenancy.view_site"))

    def test_owner_has_access_and_reserved_site_cannot_be_deleted_in_admin(self):
        owner = User.objects.create_superuser(username="owner", password="A-real-test-secret-6742")
        UserProfile.objects.create(user=owner, first_run_completed_at=timezone.now())
        self.client.force_login(owner)
        self.assertTrue(has_site_permission(owner, self.owner_site, "tenancy.view_site"))
        self.assertEqual(self.client.get(reverse("api-site-detail", args=[1])).status_code, 200)
        self.assertEqual(
            self.client.post(
                reverse("admin:tenancy_site_delete", args=[1]), {"post": "yes"}
            ).status_code,
            403,
        )
        self.assertTrue(Site.objects.filter(pk=1).exists())

    def test_owner_demotion_revokes_access(self):
        owner = User.objects.create_superuser(username="owner", password="A-real-test-secret-6742")
        UserProfile.objects.create(user=owner, first_run_completed_at=timezone.now())
        SiteMembership.objects.create(user=owner, site=self.owner_site, role=self.role)
        self.client.force_login(owner)
        owner.is_superuser = False
        owner.save()
        self.assertEqual(self.client.get(reverse("api-site-detail", args=[1])).status_code, 404)
        self.assertEqual(self.client.get(reverse("api-sites")).json()["results"], [])
