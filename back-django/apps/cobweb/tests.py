from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import Role, Site, SiteMembership

from .models import CobwebItem


class CobwebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="editor")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.site = Site.objects.create(code="cob-a", name="Site A")
        cls.other = Site.objects.create(code="cob-b", name="Site B")
        cls.role = Role.objects.create(code="cob-editor", name="Éditeur Cobweb")
        cls.role.permissions.set(Permission.objects.filter(content_type__app_label="cobweb"))
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        cls.categories = []
        for site in (cls.site, cls.other):
            lookup = LookupList.objects.create(
                site=site,
                module="cobweb",
                kind="category",
                code="categories",
                name="Catégories Cobweb",
            )
            cls.categories.append(
                LookupEntry.objects.create(lookup_list=lookup, value="links", label="Liens")
            )
        cls.item = CobwebItem.objects.create(
            site=cls.site, category=cls.categories[0], title="Alpha", reference="A-1"
        )
        cls.hidden = CobwebItem.objects.create(
            site=cls.other, category=cls.categories[1], title="Secret autre site"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, action, item=None, site=None):
        kwargs = {"site_id": (site or self.site).pk}
        if item:
            kwargs["pk"] = item.pk
        return reverse(f"cobweb:{action}", kwargs=kwargs)

    def payload(self, **overrides):
        return {
            "title": "Nouvelle entrée",
            "category": self.categories[0].pk,
            "content": "Contenu simple",
            "link": "https://example.com",
            "position": 2,
            "is_active": "on",
            "version": self.item.updated_at.isoformat(),
            **overrides,
        }

    def test_complete_crud_and_audit(self):
        response = self.client.post(self.url("create") + "?q=Nouvelle", self.payload())
        item = CobwebItem.objects.get(title="Nouvelle entrée")
        self.assertEqual(response.status_code, 302)
        self.assertIn("?q=Nouvelle", response.url)
        self.assertContains(self.client.get(response.url), "Contenu simple")
        self.assertContains(self.client.get(self.url("list"), {"q": "Nouvelle"}), item.title)
        response = self.client.post(
            self.url("edit", item),
            self.payload(title="Modifiée", version=item.updated_at.isoformat()),
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.title, "Modifiée")
        response = self.client.post(
            self.url("delete", item), {"version": item.updated_at.isoformat(), "confirm": "on"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CobwebItem.objects.filter(pk=item.pk).exists())
        events = AuditEvent.objects.filter(entity_type="cobweb.cobwebitem", entity_id=str(item.pk))
        self.assertEqual(
            set(events.values_list("action", flat=True)),
            {"cobweb.item.create", "cobweb.item.update", "cobweb.item.delete"},
        )
        self.assertTrue(
            all(
                event.actor_id == self.user.pk and event.site_id == self.site.pk for event in events
            )
        )

    def test_cross_site_access_denied_for_all_routes_and_methods(self):
        self.assertNotContains(self.client.get(self.url("list")), self.hidden.title)
        for action in ("detail", "edit", "delete"):
            for method in ("get", "post"):
                with self.subTest(action=action, method=method):
                    response = getattr(self.client, method)(
                        self.url(action, self.hidden), self.payload()
                    )
                    self.assertIn(response.status_code, (404, 405))
                    self.assertNotIn(self.hidden.title.encode(), response.content)
        self.assertEqual(self.client.get(self.url("list", site=self.other)).status_code, 404)
        self.assertEqual(
            self.client.post(self.url("create", site=self.other), self.payload()).status_code, 404
        )

    def test_anonymous_requires_login(self):
        self.client.logout()
        for action, item in (
            ("list", None),
            ("create", None),
            ("detail", self.item),
            ("edit", self.item),
            ("delete", self.item),
        ):
            self.assertEqual(self.client.get(self.url(action, item)).status_code, 302)

    def test_permissions_checked_individually_and_revocation_immediate(self):
        for action, permission, item in (
            ("create", "add", None),
            ("edit", "change", self.item),
            ("delete", "delete", self.item),
        ):
            perm = Permission.objects.get(
                content_type__app_label="cobweb", codename=f"{permission}_cobwebitem"
            )
            self.role.permissions.remove(perm)
            self.assertEqual(
                self.client.post(self.url(action, item), self.payload()).status_code, 404
            )
            self.role.permissions.add(perm)
        self.role.permissions.clear()
        self.assertEqual(self.client.get(self.url("list")).status_code, 404)
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="cobweb"))
        self.assertEqual(self.client.get(self.url("detail", self.item)).status_code, 404)

    def test_read_only_ctas_and_write_denial(self):
        self.role.permissions.set(
            Permission.objects.filter(content_type__app_label="cobweb", codename="view_cobwebitem")
        )
        response = self.client.get(self.url("list"))
        self.assertContains(response, "Consulter")
        for label in ("Nouvelle entrée", "Modifier", "Supprimer"):
            self.assertNotContains(response, label)
        self.assertEqual(
            self.client.post(self.url("edit", self.item), self.payload()).status_code, 404
        )

    def test_inactive_membership_site_or_user_denies_access(self):
        membership = SiteMembership.objects.get(user=self.user, site=self.site)
        membership.is_active = False
        membership.save()
        self.assertEqual(self.client.get(self.url("list")).status_code, 404)
        membership.is_active = True
        membership.save()
        Site.objects.filter(pk=self.site.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.url("list")).status_code, 404)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get(self.url("list")).status_code, 302)

    def test_csrf_on_every_mutation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for action, item in (("create", None), ("edit", self.item), ("delete", self.item)):
            self.assertEqual(client.post(self.url(action, item), self.payload()).status_code, 403)
        self.assertEqual(CobwebItem.objects.count(), 2)

    def test_validation_does_not_persist_partial_data(self):
        invalid = [
            dict(title=""),
            dict(category=self.categories[1].pk),
            dict(position=-1),
            dict(link="javascript:alert(1)"),
            dict(link="ftp://example.com"),
            dict(content="a" * 20001),
        ]
        for changes in invalid:
            with self.subTest(changes=list(changes)):
                response = self.client.post(self.url("create"), self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(CobwebItem.objects.count(), 2)
        self.assertFalse(AuditEvent.objects.filter(action="cobweb.item.create").exists())

    def test_model_rejects_wrong_category_scope_or_module(self):
        item = CobwebItem(site=self.site, category=self.categories[1], title="Invalid")
        with self.assertRaises(ValidationError):
            item.full_clean()
        lookup = self.categories[0].lookup_list
        lookup.module = "articles"
        lookup.save()
        item.category = LookupEntry.objects.get(pk=self.categories[0].pk)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_inactive_category_not_newly_assignable_but_existing_can_be_retained(self):
        LookupEntry.objects.filter(pk=self.categories[0].pk).update(is_active=False)
        response = self.client.post(self.url("create"), self.payload())
        self.assertIn("category", response.context["form"].errors)
        response = self.client.post(self.url("edit", self.item), self.payload(title="Retained"))
        self.assertEqual(response.status_code, 302)

    def test_mass_assignment_site_and_timestamps_ignored(self):
        response = self.client.post(
            self.url("create"), self.payload(site=self.other.pk, created_at="2000-01-01")
        )
        self.assertEqual(response.status_code, 302)
        item = CobwebItem.objects.get(title="Nouvelle entrée")
        self.assertEqual(item.site_id, self.site.pk)
        self.assertNotEqual(item.created_at.year, 2000)

    def test_xss_escaped_and_private_security_headers(self):
        payload = '<script>alert("xss")</script>'
        self.item.title = payload
        self.item.content = payload
        self.item.save()
        for url in (
            self.url("list"),
            self.url("detail", self.item),
            self.url("edit", self.item),
            self.url("delete", self.item),
        ):
            response = self.client.get(url)
            self.assertNotContains(response, payload)
            self.assertContains(response, "&lt;script&gt;")
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_search_filters_dates_and_sort(self):
        old = timezone.now() - timedelta(days=5)
        CobwebItem.objects.filter(pk=self.item.pk).update(created_at=old)
        second = CobwebItem.objects.create(
            site=self.site, category=self.categories[0], title="Beta", is_active=False
        )
        params = {
            "q": "Beta",
            "active": "no",
            "category": self.categories[0].pk,
            "created_from": timezone.localdate().isoformat(),
            "created_until": timezone.localdate().isoformat(),
            "order": "-title",
        }
        response = self.client.get(self.url("list"), params)
        self.assertEqual(list(response.context["page"].object_list), [second])
        response = self.client.get(self.url("list"), {"q": "A-1"})
        self.assertEqual(list(response.context["page"].object_list), [self.item])
        response = self.client.get(self.url("list"), {"order": "-title"})
        self.assertEqual([item.title for item in response.context["page"]], ["Beta", "Alpha"])

    def test_invalid_filters_fail_closed(self):
        for query in (
            {"category": self.categories[1].pk},
            {"order": "site__name"},
            {"created_from": "bad"},
            {"created_from": "2026-02-01", "created_until": "2026-01-01"},
        ):
            response = self.client.get(self.url("list"), query)
            self.assertTrue(response.context["search_form"].errors)
            self.assertEqual(response.context["page"].paginator.count, 0)

    def test_pagination_and_filtered_return(self):
        CobwebItem.objects.bulk_create(
            [
                CobwebItem(site=self.site, category=self.categories[0], title=f"Entry {i}")
                for i in range(25)
            ]
        )
        first = self.client.get(self.url("list"), {"page": 1}).context["page"]
        second = self.client.get(self.url("list"), {"page": 2}).context["page"]
        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 6)
        self.assertFalse({i.pk for i in first} & {i.pk for i in second})
        response = self.client.get(
            self.url("detail", self.item),
            {"q": "Alpha", "page": 2, "next": "https://attacker.example"},
        )
        self.assertEqual(response.context["return_url"], self.url("list") + "?q=Alpha&page=2")

    def test_delete_requires_confirmation_and_get_is_safe(self):
        self.assertEqual(self.client.get(self.url("delete", self.item)).status_code, 200)
        response = self.client.post(
            self.url("delete", self.item), {"version": self.item.updated_at.isoformat()}
        )
        self.assertIn("confirm", response.context["form"].errors)
        self.assertTrue(CobwebItem.objects.filter(pk=self.item.pk).exists())

    def test_stale_update_and_delete_rejected(self):
        version = self.item.updated_at.isoformat()
        CobwebItem.objects.filter(pk=self.item.pk).update(
            title="Concurrent", updated_at=timezone.now()
        )
        self.assertEqual(
            self.client.post(
                self.url("edit", self.item), self.payload(version=version)
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                self.url("delete", self.item), {"version": version, "confirm": "on"}
            ).status_code,
            409,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, "Concurrent")
        self.assertFalse(AuditEvent.objects.filter(entity_type="cobweb.cobwebitem").exists())

    def test_audit_failure_rolls_back_write_and_delete(self):
        with patch("apps.cobweb.views.audit", side_effect=IntegrityError("audit failure")):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url("create"), self.payload())
            with self.assertRaises(IntegrityError):
                self.client.post(
                    self.url("delete", self.item),
                    {"version": self.item.updated_at.isoformat(), "confirm": "on"},
                )
        self.assertEqual(CobwebItem.objects.count(), 2)

    def test_http_methods(self):
        self.assertEqual(self.client.post(self.url("list")).status_code, 405)
        self.assertEqual(self.client.post(self.url("detail", self.item)).status_code, 405)
        self.assertEqual(self.client.put(self.url("edit", self.item)).status_code, 405)

    def test_workspace_hides_unauthorized_module(self):
        url = reverse("site-workspace", args=[self.site.pk])
        self.assertContains(self.client.get(url), f'href="{self.url("list")}"')
        self.role.permissions.clear()
        self.assertNotContains(self.client.get(url), 'href="' + self.url("list") + '"')


class DemoSetupTests(TestCase):
    def test_demo_command_refuses_production(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("prepare_cobweb_demo")

    def test_demo_setup_is_idempotent_and_scoped(self):
        from io import StringIO

        from django.core.management import call_command

        user = get_user_model().objects.create_user(username="demo")
        site = Site.objects.create(code="demo", name="Site de démonstration")
        role = Role.objects.create(code="demo-site-admin", name="Admin demo")
        role.permissions.add(
            Permission.objects.get(
                content_type__app_label="tenancy", codename="manage_site_settings"
            )
        )
        SiteMembership.objects.create(user=user, site=site, role=role)
        with self.settings(DEBUG=True):
            call_command("prepare_cobweb_demo", stdout=StringIO())
            self.assertEqual(CobwebItem.objects.filter(site=site).count(), 3)
            item = CobwebItem.objects.get(reference="COB-DEMO-01")
            item.title = "Titre personnalisé conservé"
            item.save()
            audit_count = AuditEvent.objects.count()
            call_command("prepare_cobweb_demo", stdout=StringIO())
        self.assertEqual(CobwebItem.objects.count(), 3)
        self.assertEqual(AuditEvent.objects.count(), audit_count)
        item.refresh_from_db()
        self.assertEqual(item.title, "Titre personnalisé conservé")
        self.assertEqual(
            role.permissions.filter(
                content_type__app_label="cobweb",
                content_type__model__in=["cobwebitem", "cobwebgroup"],
            ).count(),
            10,
        )

    def test_demo_command_refuses_role_shared_across_sites(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        user = get_user_model().objects.create_user(username="demo")
        site = Site.objects.create(code="demo", name="Demo")
        other = Site.objects.create(code="other", name="Other")
        role = Role.objects.create(code="demo-site-admin", name="Admin demo")
        SiteMembership.objects.create(user=user, site=site, role=role)
        SiteMembership.objects.create(user=user, site=other, role=role)
        with self.settings(DEBUG=True), self.assertRaises(CommandError):
            call_command("prepare_cobweb_demo")
        self.assertEqual(role.permissions.count(), 0)
        self.assertEqual(CobwebItem.objects.count(), 0)
