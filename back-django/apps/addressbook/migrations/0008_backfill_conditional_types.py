"""Install site-local types and preserve existing address and extra-info values."""

from django.db import migrations


TYPE_LISTS = (
    (
        "addressbook_address.address_type",
        "starter-addressbook-address-types",
        "Types d’adresse",
        (
            ("home", "Domicile", "", ""),
            ("work", "Professionnelle", "", ""),
            ("billing", "Facturation", "", ""),
            ("other", "Autre", "", ""),
        ),
    ),
    (
        "addressbook_extrainfo.info_type",
        "starter-addressbook-info-types",
        "Catégories d’information privée",
        (
            ("text", "Texte libre", "", "text"),
            ("date", "Date", "", "date"),
            ("number", "Nombre", "", "number"),
            ("boolean", "Oui / non", "", "boolean"),
        ),
    ),
)


def backfill_types(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    LookupList = apps.get_model("configuration", "LookupList")
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    Address = apps.get_model("addressbook", "Address")
    ExtraInfo = apps.get_model("addressbook", "ExtraInfo")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    for site in Site.objects.using(alias).exclude(pk=1).order_by("pk").iterator():
        for field_key, code, name, values in TYPE_LISTS:
            lookup = LookupList.objects.using(alias).filter(
                site_id=site.pk, module="addressbook", field_key=field_key
            ).first()
            if lookup is None:
                lookup, created = LookupList.objects.using(alias).get_or_create(
                    site_id=site.pk,
                    module="addressbook",
                    kind="choice",
                    code=code,
                    defaults={"field_key": field_key, "name": name},
                )
                if created:
                    AuditEvent.objects.using(alias).create(
                        site_id=site.pk,
                        action="setup.addressbook.reference.create",
                        entity_type="configuration.lookuplist",
                        entity_id=str(lookup.pk),
                    )
            if lookup.kind != "choice" or lookup.field_key != field_key:
                raise ValueError(f"Le référentiel {code!r} du site {site.pk} cible un autre champ.")
            refs = {}
            for value, label, family, value_kind in values:
                item, created = LookupEntry.objects.using(alias).get_or_create(
                    lookup_list_id=lookup.pk,
                    value=value,
                    defaults={
                        "label": label,
                        "addressbook_kind": family,
                        "value_kind": value_kind,
                    },
                )
                if created:
                    AuditEvent.objects.using(alias).create(
                        site_id=site.pk,
                        action="setup.addressbook.reference_entry.create",
                        entity_type="configuration.lookupentry",
                        entity_id=str(item.pk),
                    )
                refs[value] = item.pk
            if field_key == "addressbook_address.address_type":
                for row in Address.objects.using(alias).filter(entry__site_id=site.pk).iterator():
                    if row.kind not in refs:
                        raise ValueError(f"Type d’adresse inconnu pour la ligne {row.pk}.")
                    row.address_type_id = refs[row.kind]
                    row.save(update_fields=["address_type"])
            else:
                for row in ExtraInfo.objects.using(alias).filter(entry__site_id=site.pk).iterator():
                    if row.kind not in refs:
                        raise ValueError(f"Format d’information inconnu pour la ligne {row.pk}.")
                    row.info_type_id = refs[row.kind]
                    row.save(update_fields=["info_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("addressbook", "0007_add_conditional_type_references"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(backfill_types, migrations.RunPython.noop)]
