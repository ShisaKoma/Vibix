"""Align pre-existing starter genders with the person-only installation seed."""

from django.db import migrations


STARTER_GENDERS = ("woman", "man", "nonbinary", "undisclosed")


def scope_starter_genders(apps, schema_editor):
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    entries = LookupEntry.objects.using(alias).filter(
        lookup_list__module="addressbook",
        lookup_list__field_key="addressbook_entry.gender",
        lookup_list__code="starter-addressbook-genders",
        value__in=STARTER_GENDERS,
        addressbook_kind="",
    )
    for item in entries.iterator():
        item.addressbook_kind = "person"
        item.save(update_fields=["addressbook_kind"])
        AuditEvent.objects.using(alias).create(
            site_id=item.lookup_list.site_id,
            action="setup.addressbook.reference_entry.scope",
            entity_type="configuration.lookupentry",
            entity_id=str(item.pk),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("addressbook", "0005_seed_conditional_references"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(scope_starter_genders, migrations.RunPython.noop)]
