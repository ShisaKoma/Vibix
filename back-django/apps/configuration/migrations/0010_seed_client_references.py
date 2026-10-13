from django.db import migrations


CLIENT_LISTS = (
    (
        "api_client.template",
        "starter-client-templates",
        "Templates des fronts clients",
        (("bloc", "Bloc"), ("shape", "Shape"), ("custom", "Custom")),
    ),
    (
        "api_client.color_scheme",
        "starter-client-color-schemes",
        "Ambiances des fronts clients",
        (("light", "Light"), ("dark", "Dark")),
    ),
)


def seed_references(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    LookupList = apps.get_model("configuration", "LookupList")
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    for site in Site.objects.using(alias).exclude(pk=1).order_by("pk").iterator():
        for field, code, name, values in CLIENT_LISTS:
            lookup, created = LookupList.objects.using(alias).get_or_create(
                site_id=site.pk,
                module="api",
                kind="choice",
                code=code,
                defaults={"name": name, "field_key": field},
            )
            if lookup.field_key != field:
                raise ValueError(f"Le référentiel {code!r} cible un autre champ.")
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk,
                    action="setup.client.reference.create",
                    entity_type="configuration.lookuplist",
                    entity_id=str(lookup.pk),
                )
            for position, (value, label) in enumerate(values):
                entry, created = LookupEntry.objects.using(alias).get_or_create(
                    lookup_list_id=lookup.pk,
                    value=value,
                    defaults={"label": label, "position": position},
                )
                if created:
                    AuditEvent.objects.using(alias).create(
                        site_id=site.pk,
                        action="setup.client.reference_entry.create",
                        entity_type="configuration.lookupentry",
                        entity_id=str(entry.pk),
                    )


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0009_client_reference_fields"),
        ("tenancy", "0002_reserve_owner_site"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(seed_references, migrations.RunPython.noop)]
