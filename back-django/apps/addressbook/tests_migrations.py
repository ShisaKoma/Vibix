"""Exercise the historical-row conversion before removing legacy type columns."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ConditionalTypeMigrationTests(TransactionTestCase):
    def test_existing_address_and_extra_info_keep_their_values(self):
        executor = MigrationExecutor(connection)
        old_target = [("addressbook", "0006_scope_starter_genders")]
        executor.migrate(old_target)
        old = executor.loader.project_state(old_target).apps
        Site = old.get_model("tenancy", "Site")
        EntryType = old.get_model("addressbook", "EntryType")
        Entry = old.get_model("addressbook", "Entry")
        Address = old.get_model("addressbook", "Address")
        ExtraInfo = old.get_model("addressbook", "ExtraInfo")
        site = Site.objects.create(code="migration-addressbook", name="Migration carnet")
        entry_type = EntryType.objects.create(
            site_id=site.pk, code="person", kind="person", label="Personne"
        )
        entry = Entry.objects.create(
            site_id=site.pk, kind="person", entry_type_id=entry_type.pk, name="Martin"
        )
        address = Address.objects.create(
            entry_id=entry.pk,
            kind="home",
            street="1 rue de test",
            city="Paris",
            country="France",
        )
        info = ExtraInfo.objects.create(
            entry_id=entry.pk, label="Échéance", kind="date", value="2026-10-02"
        )

        latest = [("addressbook", "0009_require_conditional_types")]
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        current = executor.loader.project_state(latest).apps
        migrated_address = current.get_model("addressbook", "Address").objects.get(pk=address.pk)
        migrated_info = current.get_model("addressbook", "ExtraInfo").objects.get(pk=info.pk)
        self.assertEqual(migrated_address.address_type.value, "home")
        self.assertEqual(migrated_address.street, "1 rue de test")
        self.assertEqual(migrated_info.info_type.value, "date")
        self.assertEqual(migrated_info.info_type.value_kind, "date")
        self.assertEqual(migrated_info.value, "2026-10-02")
