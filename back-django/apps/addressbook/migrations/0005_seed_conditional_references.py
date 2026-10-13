"""Add usable site-local civilities and legal forms without overwriting custom data."""

from django.db import migrations


STARTER_CHOICES = (
    (
        "addressbook_entry.salutation",
        "starter-addressbook-salutations",
        "Civilités",
        (("madame", "Madame", "person"), ("monsieur", "Monsieur", "person")),
    ),
    (
        "addressbook_entry.legal_form",
        "starter-addressbook-legal-forms",
        "Formes juridiques",
        (
            ("entreprise-individuelle", "Entreprise individuelle", "company"),
            ("sarl", "SARL", "company"),
            ("sas", "SAS", "company"),
            ("sa", "SA", "company"),
            ("association-loi-1901", "Association loi 1901", "association"),
        ),
    ),
)


def seed_choices(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    LookupList = apps.get_model("configuration", "LookupList")
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    for site in Site.objects.using(alias).exclude(pk=1).order_by("pk").iterator():
        for field_key, code, name, values in STARTER_CHOICES:
            lookup = LookupList.objects.using(alias).filter(
                site_id=site.pk, module="addressbook", field_key=field_key
            ).first()
            created = False
            if lookup is None:
                lookup, created = LookupList.objects.using(alias).get_or_create(
                    site_id=site.pk,
                    module="addressbook",
                    kind="choice",
                    code=code,
                    defaults={"field_key": field_key, "name": name},
                )
            if lookup.kind != "choice" or lookup.field_key != field_key:
                raise ValueError(f"Le référentiel {code!r} du site {site.pk} cible un autre champ.")
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk,
                    action="setup.addressbook.reference.create",
                    entity_type="configuration.lookuplist",
                    entity_id=str(lookup.pk),
                )
            if lookup.code != code:
                continue
            for value, label, kind in values:
                item, created = LookupEntry.objects.using(alias).get_or_create(
                    lookup_list_id=lookup.pk,
                    value=value,
                    defaults={"label": label, "addressbook_kind": kind},
                )
                if created:
                    AuditEvent.objects.using(alias).create(
                        site_id=site.pk,
                        action="setup.addressbook.reference_entry.create",
                        entity_type="configuration.lookupentry",
                        entity_id=str(item.pk),
                    )


class Migration(migrations.Migration):
    dependencies = [
        ("addressbook", "0004_entry_type_required"),
        ("configuration", "0013_addressbook_reference_kind"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(seed_choices, migrations.RunPython.noop)]
