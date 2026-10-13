from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SlideshowReferenceMigrationTests(TransactionTestCase):
    migrate_from = [
        ("cobweb", "0004_cobwebgroup_picture_cobwebgroup_presentation_and_more"),
        ("configuration", "0005_alter_lookuplist_field_key"),
        ("tenancy", "0004_site_description_site_icon_site_primary_color_1_and_more"),
        ("audit", "0002_initial"),
    ]
    migrate_to = [("cobweb", "0005_seed_slideshow_references")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(self.restore_migrations)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def restore_migrations(self):
        MigrationExecutor(connection).migrate(self.latest)

    def test_existing_sites_receive_references_without_overwriting_customizations(self):
        Site = self.old_apps.get_model("tenancy", "Site")
        LookupList = self.old_apps.get_model("configuration", "LookupList")
        LookupEntry = self.old_apps.get_model("configuration", "LookupEntry")
        owner, _ = Site.objects.get_or_create(pk=1, defaults={"code": "owner", "name": "Owner"})
        active = Site.objects.create(code="migration-active", name="Actif")
        inactive = Site.objects.create(code="migration-inactive", name="Inactif", is_active=False)
        custom = Site.objects.create(code="migration-custom", name="Personnalisé")
        for kind, code, field_key in (
            ("category", "starter-cobweb-groups", "cobweb_group.category"),
            ("choice", "starter-cobweb-presentations", "cobweb_group.presentation"),
        ):
            lookup = LookupList.objects.create(
                site=custom,
                module="cobweb",
                kind=kind,
                code=code,
                field_key=field_key,
                name="Nom personnalisé",
                is_active=False,
            )
            LookupEntry.objects.create(
                lookup_list=lookup,
                value="slideshow",
                label="Libellé personnalisé",
                position=19,
                is_active=False,
            )
        custom_lists = list(LookupList.objects.filter(site=custom).order_by("pk").values())
        custom_entries = list(
            LookupEntry.objects.filter(lookup_list__site=custom).order_by("pk").values()
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        LookupList = apps.get_model("configuration", "LookupList")
        LookupEntry = apps.get_model("configuration", "LookupEntry")
        AuditEvent = apps.get_model("audit", "AuditEvent")
        for site in (active, inactive):
            for field_key, label in (
                ("cobweb_group.category", "Diaporamas"),
                ("cobweb_group.presentation", "Diaporama"),
            ):
                with self.subTest(site=site.code, field_key=field_key):
                    entry = LookupEntry.objects.get(
                        lookup_list__site_id=site.pk,
                        lookup_list__field_key=field_key,
                        value="slideshow",
                    )
                    self.assertEqual(entry.label, label)
                    self.assertTrue(entry.is_active)
                    self.assertTrue(entry.lookup_list.is_active)
            events = AuditEvent.objects.filter(site_id=site.pk)
            self.assertEqual(events.count(), 4)
            self.assertEqual(set(events.values_list("actor_id", flat=True)), {None})
            self.assertEqual(
                set(events.values_list("entity_type", "entity_id")),
                {
                    ("configuration.lookuplist", str(pk))
                    for pk in LookupList.objects.filter(site_id=site.pk).values_list(
                        "pk", flat=True
                    )
                }
                | {
                    ("configuration.lookupentry", str(pk))
                    for pk in LookupEntry.objects.filter(lookup_list__site_id=site.pk).values_list(
                        "pk", flat=True
                    )
                },
            )
        self.assertFalse(LookupList.objects.filter(site_id=owner.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(site_id=owner.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(site_id=custom.pk).exists())
        self.assertEqual(
            list(LookupList.objects.filter(site_id=custom.pk).order_by("pk").values()), custom_lists
        )
        self.assertEqual(
            list(
                LookupEntry.objects.filter(lookup_list__site_id=custom.pk).order_by("pk").values()
            ),
            custom_entries,
        )
        self.assertFalse(apps.get_model("cobweb", "CobwebGroup").objects.exists())
        self.assertFalse(apps.get_model("cobweb", "CobwebItem").objects.exists())

        before_lists = list(LookupList.objects.order_by("pk").values())
        before_entries = list(LookupEntry.objects.order_by("pk").values())
        before_events = list(AuditEvent.objects.order_by("pk").values())
        migration = import_module("apps.cobweb.migrations.0005_seed_slideshow_references")
        with connection.schema_editor() as editor:
            migration.seed_slideshow_references(apps, editor)
        self.assertEqual(list(LookupList.objects.order_by("pk").values()), before_lists)
        self.assertEqual(list(LookupEntry.objects.order_by("pk").values()), before_entries)
        self.assertEqual(list(AuditEvent.objects.order_by("pk").values()), before_events)
