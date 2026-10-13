from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.media.models import Asset
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Role, Site, SiteMembership

from .models import CobwebGroup, CobwebItem
from .seeds import seed_cobweb_references

User = get_user_model()


class CobwebReferenceInitializationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code="seed-a", name="Site de test A")
        cls.other_site = Site.objects.create(code="seed-b", name="Site de test B")
        cls.owner_site = Site.objects.get(pk=OWNER_SITE_ID)

    def assert_references(self, site):
        expected = {
            "cobweb_item.category": (
                "category",
                {
                    "medias": "Médias",
                    "advertisements": "Publicités",
                    "partners": "Partenaires",
                    "sitemap": "Sitemap",
                },
            ),
            "cobweb_group.category": (
                "category",
                {
                    "medias": "Groupes de médias",
                    "slideshow": "Diaporamas",
                    "advertisements": "Groupes de publicités",
                    "partners": "Groupes de partenaires",
                    "sitemap": "Groupes de sitemap",
                },
            ),
            "cobweb_item.kind": (
                "choice",
                {
                    "image": "Image",
                    "advertisement": "Publicité",
                    "partner": "Partenaire",
                    "link": "Lien",
                    "planning": "Planning",
                },
            ),
            "cobweb_group.presentation": (
                "choice",
                {"cards": "Cartes", "list": "Liste", "slideshow": "Diaporama"},
            ),
        }
        lists = LookupList.objects.filter(site=site, module="cobweb")
        self.assertEqual(lists.count(), 4)
        self.assertEqual(set(lists.values_list("field_key", flat=True)), set(expected))
        for field_key, (kind, entries) in expected.items():
            with self.subTest(field_key=field_key):
                lookup = lists.get(field_key=field_key)
                self.assertEqual(lookup.module, "cobweb")
                self.assertEqual(lookup.kind, kind)
                self.assertTrue(lookup.is_active)
                self.assertEqual(dict(lookup.entries.values_list("value", "label")), entries)
                self.assertFalse(lookup.entries.filter(is_active=False).exists())

    def assert_seed_audit(self, site, actor=None):
        events = AuditEvent.objects.filter(site=site, action__startswith="setup.cobweb.")
        expected = {
            ("setup.cobweb.reference.create", "configuration.lookuplist", str(pk))
            for pk in LookupList.objects.filter(site=site, module="cobweb").values_list(
                "pk", flat=True
            )
        } | {
            ("setup.cobweb.reference_entry.create", "configuration.lookupentry", str(pk))
            for pk in LookupEntry.objects.filter(
                lookup_list__site=site, lookup_list__module="cobweb"
            ).values_list("pk", flat=True)
        }
        self.assertEqual(events.count(), 21)
        self.assertEqual(set(events.values_list("action", "entity_type", "entity_id")), expected)
        self.assertEqual(
            set(events.values_list("actor_id", flat=True)), {actor.pk if actor else None}
        )

    def run_command(self, *arguments):
        output = StringIO()
        call_command("seed_initial_data", *arguments, stdout=output)
        return output.getvalue()

    def create_conflicting_list(self, site):
        return LookupList.objects.create(
            site=site,
            module="cobweb",
            kind="choice",
            code="starter-cobweb-presentations",
            field_key="cobweb_item.kind",
            name="Référentiel personnalisé incompatible",
        )

    def test_command_initializes_only_requested_site_without_content_or_access(self):
        counts_before = {
            model: model.objects.count()
            for model in (User, Role, SiteMembership, CobwebItem, CobwebGroup, Asset)
        }
        output = self.run_command("--site-code", self.site.code)
        self.assertIn("seed-a: 21 référence(s) créée(s).", output)
        self.assert_references(self.site)
        self.assert_seed_audit(self.site)
        self.assertFalse(LookupList.objects.filter(site=self.other_site).exists())
        self.assertFalse(AuditEvent.objects.filter(site=self.other_site).exists())
        for model, count in counts_before.items():
            with self.subTest(model=model._meta.label):
                self.assertEqual(model.objects.count(), count)

    def test_repeated_command_preserves_identifiers_and_does_not_duplicate_audit(self):
        self.run_command("--site-code", self.site.code)
        lists_before = list(LookupList.objects.filter(site=self.site).order_by("pk").values())
        entries_before = list(
            LookupEntry.objects.filter(lookup_list__site=self.site).order_by("pk").values()
        )
        audits_before = list(AuditEvent.objects.order_by("pk").values())
        output = self.run_command("--site-code", self.site.code)
        self.assertIn("seed-a: 0 référence(s) créée(s).", output)
        self.assertEqual(
            list(LookupList.objects.filter(site=self.site).order_by("pk").values()), lists_before
        )
        self.assertEqual(
            list(LookupEntry.objects.filter(lookup_list__site=self.site).order_by("pk").values()),
            entries_before,
        )
        self.assertEqual(list(AuditEvent.objects.order_by("pk").values()), audits_before)

    def test_all_sites_includes_inactive_sites_excludes_owner_and_is_idempotent(self):
        self.other_site.is_active = False
        self.other_site.save(update_fields=["is_active"])
        output = self.run_command("--all-sites")
        self.assertIn("42 liste(s) ou valeur(s) créée(s).", output)
        for site in (self.site, self.other_site):
            self.assert_references(site)
            self.assert_seed_audit(site)
        self.assertFalse(LookupList.objects.filter(site=self.owner_site).exists())
        self.assertFalse(AuditEvent.objects.filter(site=self.owner_site).exists())
        self.assertIn("0 liste(s) ou valeur(s) créée(s).", self.run_command("--all-sites"))
        self.assertEqual(AuditEvent.objects.filter(action__startswith="setup.cobweb.").count(), 42)

    def test_owner_is_ignored_by_service_and_refused_by_explicit_command(self):
        self.assertEqual(seed_cobweb_references(self.owner_site), 0)
        with self.assertRaisesMessage(CommandError, "Le site réservé 1"):
            self.run_command("--site-code", self.owner_site.code)
        self.assertFalse(LookupList.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_unknown_site_is_refused_without_writes(self):
        with self.assertRaisesMessage(CommandError, "Aucun site ne correspond à ce code."):
            self.run_command("--site-code", "missing-site")
        self.assertFalse(LookupList.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_customized_references_are_preserved_and_missing_defaults_are_added(self):
        lookup = LookupList.objects.create(
            site=self.site,
            module="cobweb",
            kind="category",
            code="starter-cobweb-items",
            field_key="cobweb_item.category",
            name="Nos rubriques",
            is_active=False,
        )
        parent = LookupEntry.objects.create(lookup_list=lookup, value="custom", label="Sur mesure")
        entry = LookupEntry.objects.create(
            lookup_list=lookup,
            value="medias",
            label="Nos visuels",
            parent=parent,
            position=42,
            is_active=False,
        )
        list_before = LookupList.objects.filter(pk=lookup.pk).values().get()
        entry_before = LookupEntry.objects.filter(pk=entry.pk).values().get()
        parent_before = LookupEntry.objects.filter(pk=parent.pk).values().get()
        output = self.run_command("--site-code", self.site.code)
        self.assertIn("19 référence(s) créée(s).", output)
        self.assertEqual(LookupList.objects.filter(pk=lookup.pk).values().get(), list_before)
        self.assertEqual(LookupEntry.objects.filter(pk=entry.pk).values().get(), entry_before)
        self.assertEqual(LookupEntry.objects.filter(pk=parent.pk).values().get(), parent_before)
        self.assertEqual(LookupList.objects.filter(site=self.site).count(), 4)
        self.assertEqual(LookupEntry.objects.filter(lookup_list__site=self.site).count(), 18)
        self.assertEqual(lookup.entries.get(value="advertisements").label, "Publicités")
        events = AuditEvent.objects.filter(site=self.site, action__startswith="setup.cobweb.")
        self.assertEqual(events.count(), 19)
        self.assertFalse(
            events.filter(entity_type="configuration.lookuplist", entity_id=str(lookup.pk)).exists()
        )
        self.assertFalse(
            events.filter(
                entity_type="configuration.lookupentry",
                entity_id__in=[str(entry.pk), str(parent.pk)],
            ).exists()
        )

    def test_late_field_conflict_rolls_back_references_and_audit(self):
        conflict = self.create_conflicting_list(self.site)
        before = LookupList.objects.filter(pk=conflict.pk).values().get()
        with self.assertRaisesMessage(ValueError, "cible un autre champ"):
            seed_cobweb_references(self.site)
        self.assertEqual(list(LookupList.objects.values_list("pk", flat=True)), [conflict.pk])
        self.assertEqual(LookupList.objects.filter(pk=conflict.pk).values().get(), before)
        self.assertFalse(LookupEntry.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_all_sites_failure_rolls_back_previously_prepared_site(self):
        conflict = self.create_conflicting_list(self.other_site)
        with self.assertRaisesMessage(ValueError, "cible un autre champ"):
            self.run_command("--all-sites")
        self.assertFalse(LookupList.objects.filter(site=self.site).exists())
        self.assertEqual(list(LookupList.objects.values_list("pk", flat=True)), [conflict.pk])
        self.assertFalse(LookupEntry.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_audit_failure_rolls_back_service_writes(self):
        create_event = AuditEvent.objects.create

        def fail_entry_audit(**kwargs):
            if kwargs["action"] == "setup.cobweb.reference_entry.create":
                raise RuntimeError("audit indisponible")
            return create_event(**kwargs)

        with patch(
            "apps.cobweb.seeds.AuditEvent.objects.create",
            side_effect=fail_entry_audit,
        ):
            with self.assertRaisesMessage(RuntimeError, "audit indisponible"):
                seed_cobweb_references(self.site)
        self.assertFalse(LookupList.objects.exists())
        self.assertFalse(LookupEntry.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def login_admin(self):
        self.root = User.objects.create_superuser(username="seed-admin", password="Test-seed-6742!")
        UserProfile.objects.create(user=self.root, first_run_completed_at=timezone.now())
        self.client.force_login(self.root)

    def add_site(self):
        return self.client.post(
            reverse("admin:tenancy_site_add"),
            {
                "code": "admin-seed",
                "name": "Site créé dans l’administration",
                "is_active": "on",
                "_save": "1",
                "primary_color_1": "#175EC5",
                "primary_color_2": "#172B43",
                "secondary_color_1": "#E6EDFC",
                "secondary_color_2": "#F3F5F8",
            },
        )

    def test_admin_creation_initializes_references_and_records_actor(self):
        self.login_admin()
        self.assertEqual(self.add_site().status_code, 302)
        site = Site.objects.get(code="admin-seed")
        self.assert_references(site)
        self.assert_seed_audit(site, actor=self.root)
        event = AuditEvent.objects.get(action="admin.create", entity_type="tenancy.site")
        self.assertEqual(event.actor, self.root)
        self.assertEqual(event.entity_id, str(site.pk))
        self.assertFalse(SiteMembership.objects.filter(site=site).exists())
        self.assertFalse(CobwebItem.objects.filter(site=site).exists())
        self.assertFalse(CobwebGroup.objects.filter(site=site).exists())
        self.assertFalse(Asset.objects.filter(site=site).exists())

    def test_admin_audit_failure_rolls_back_site_creation(self):
        self.login_admin()
        create_event = AuditEvent.objects.create

        def fail_entry_audit(**kwargs):
            if kwargs["action"] == "setup.cobweb.reference_entry.create":
                raise RuntimeError("audit indisponible")
            return create_event(**kwargs)

        with patch(
            "apps.cobweb.seeds.AuditEvent.objects.create",
            side_effect=fail_entry_audit,
        ):
            with self.assertRaisesMessage(RuntimeError, "audit indisponible"):
                self.add_site()
        self.assertFalse(Site.objects.filter(code="admin-seed").exists())
        self.assertFalse(LookupList.objects.exists())
        self.assertFalse(LookupEntry.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action__startswith="setup.cobweb.").exists())
        self.assertFalse(AuditEvent.objects.filter(action="admin.create").exists())
