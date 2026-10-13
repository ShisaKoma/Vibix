"""Bootstrap site-local types and lookup lists; preserve existing labels and rows."""

from django.db import migrations


TYPES = (
    ("person", "person", "Personne"),
    ("company", "company", "Entreprise"),
    ("association", "association", "Association"),
)
LISTS = (
    ("category", "addressbook_entry.category", "starter-addressbook-categories", "Catégories", ()),
    (
        "choice", "addressbook_entry.gender", "starter-addressbook-genders", "Genres",
        (("woman", "Femme"), ("man", "Homme"), ("nonbinary", "Non binaire"),
         ("undisclosed", "Non communiqué")),
    ),
    ("choice", "addressbook_entry.salutation", "starter-addressbook-salutations", "Civilités", ()),
    ("choice", "addressbook_entry.legal_form", "starter-addressbook-legal-forms", "Formes juridiques", ()),
    ("choice", "addressbook_entry.origin", "starter-addressbook-origins", "Origines", ()),
    (
        "choice", "addressbook_employment.function_type", "starter-addressbook-functions",
        "Fonctions professionnelles", (),
    ),
)


def seed_and_backfill(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    Entry = apps.get_model("addressbook", "Entry")
    EntryType = apps.get_model("addressbook", "EntryType")
    LookupList = apps.get_model("configuration", "LookupList")
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    for site in Site.objects.using(alias).exclude(pk=1).order_by("pk").iterator():
        for kind, code, label in TYPES:
            item, created = EntryType.objects.using(alias).get_or_create(
                site_id=site.pk, code=code, defaults={"kind": kind, "label": label}
            )
            if item.kind != kind:
                raise ValueError(f"Le type {code!r} du site {site.pk} appartient à une autre famille.")
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk, action="setup.addressbook.type.create",
                    entity_type="addressbook.entrytype", entity_id=str(item.pk),
                )
            Entry.objects.using(alias).filter(site_id=site.pk, kind=kind, entry_type__isnull=True).update(
                entry_type_id=item.pk
            )
        for kind, field_key, code, name, values in LISTS:
            existing = LookupList.objects.using(alias).filter(
                site_id=site.pk, module="addressbook", field_key=field_key
            ).first()
            if existing is None:
                lookup, created = LookupList.objects.using(alias).get_or_create(
                    site_id=site.pk, module="addressbook", kind=kind, code=code,
                    defaults={"field_key": field_key, "name": name},
                )
            else:
                lookup, created = existing, False
            if lookup.kind != kind or lookup.field_key != field_key:
                raise ValueError(f"Le référentiel {code!r} du site {site.pk} cible un autre champ.")
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk, action="setup.addressbook.reference.create",
                    entity_type="configuration.lookuplist", entity_id=str(lookup.pk),
                )
            if lookup.code != code:
                continue
            for value, label in values:
                item, created = LookupEntry.objects.using(alias).get_or_create(
                    lookup_list_id=lookup.pk, value=value, defaults={"label": label}
                )
                if created:
                    AuditEvent.objects.using(alias).create(
                        site_id=site.pk, action="setup.addressbook.reference_entry.create",
                        entity_type="configuration.lookupentry", entity_id=str(item.pk),
                    )


class Migration(migrations.Migration):
    dependencies = [
        ("addressbook", "0002_employment_function_type_entry_gender_and_more"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(seed_and_backfill, migrations.RunPython.noop)]
