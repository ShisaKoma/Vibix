from django.db import migrations


def seed_slideshow_references(apps, schema_editor):
    # Historical models keep this migration independent of future seed services.
    Site = apps.get_model("tenancy", "Site")
    LookupList = apps.get_model("configuration", "LookupList")
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    specifications = (
        (
            "category",
            "starter-cobweb-groups",
            "cobweb_group.category",
            "Compositions Cobweb",
            "Diaporamas",
        ),
        (
            "choice",
            "starter-cobweb-presentations",
            "cobweb_group.presentation",
            "Présentations Cobweb",
            "Diaporama",
        ),
    )
    for site in Site.objects.using(alias).exclude(pk=1).order_by("pk").iterator():
        for kind, code, field_key, name, label in specifications:
            lookup, created = LookupList.objects.using(alias).get_or_create(
                site_id=site.pk,
                module="cobweb",
                kind=kind,
                code=code,
                defaults={"field_key": field_key, "name": name},
            )
            if lookup.field_key != field_key:
                raise ValueError(f"Le référentiel {code!r} du site {site.pk} cible un autre champ.")
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk,
                    action="setup.cobweb.reference.create",
                    entity_type="configuration.lookuplist",
                    entity_id=str(lookup.pk),
                )
            entry, created = LookupEntry.objects.using(alias).get_or_create(
                lookup_list_id=lookup.pk,
                value="slideshow",
                defaults={"label": label},
            )
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk,
                    action="setup.cobweb.reference_entry.create",
                    entity_type="configuration.lookupentry",
                    entity_id=str(entry.pk),
                )


class Migration(migrations.Migration):
    dependencies = [
        ("cobweb", "0004_cobwebgroup_picture_cobwebgroup_presentation_and_more"),
        ("configuration", "0005_alter_lookuplist_field_key"),
        ("tenancy", "0002_reserve_owner_site"),
        ("audit", "0002_initial"),
    ]
    # Keep references on reverse: they may already be used by client groups.
    operations = [migrations.RunPython(seed_slideshow_references, migrations.RunPython.noop)]
