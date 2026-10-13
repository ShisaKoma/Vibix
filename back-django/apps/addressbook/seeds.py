"""Site-local addressbook references; stable codes without synthetic contacts."""

from django.db import transaction

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.constants import OWNER_SITE_ID

from .models import EntryType

STARTER_TYPES = (
    ("person", "person", "Personne"),
    ("company", "company", "Entreprise"),
    ("association", "association", "Association"),
)
STARTER_LISTS = (
    ("category", "addressbook_entry.category", "starter-addressbook-categories", "Catégories", ()),
    (
        "choice",
        "addressbook_entry.gender",
        "starter-addressbook-genders",
        "Genres",
        (
            ("woman", "Femme", "person"),
            ("man", "Homme", "person"),
            ("nonbinary", "Non binaire", "person"),
            ("undisclosed", "Non communiqué", "person"),
        ),
    ),
    (
        "choice",
        "addressbook_entry.salutation",
        "starter-addressbook-salutations",
        "Civilités",
        (("madame", "Madame", "person"), ("monsieur", "Monsieur", "person")),
    ),
    (
        "choice",
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
    (
        "choice",
        "addressbook_address.address_type",
        "starter-addressbook-address-types",
        "Types d’adresse",
        (
            ("home", "Domicile", ""),
            ("work", "Professionnelle", ""),
            ("billing", "Facturation", ""),
            ("other", "Autre", ""),
        ),
    ),
    (
        "choice",
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
    ("choice", "addressbook_entry.origin", "starter-addressbook-origins", "Origines", ()),
    (
        "choice",
        "addressbook_employment.function_type",
        "starter-addressbook-functions",
        "Fonctions professionnelles",
        (),
    ),
)


@transaction.atomic
def seed_addressbook_references(site, *, actor=None):
    """Prepare empty/configurable lists and three stable families for one client site."""
    if site.pk == OWNER_SITE_ID:
        return 0
    created_count = 0
    for kind, code, label in STARTER_TYPES:
        entry_type, created = EntryType.objects.get_or_create(
            site=site, code=code, defaults={"kind": kind, "label": label}
        )
        if entry_type.kind != kind:
            raise ValueError(f"Le type {code!r} du site {site.pk} appartient à une autre famille.")
        if created:
            created_count += 1
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="setup.addressbook.type.create",
                entity_type="addressbook.entrytype",
                entity_id=str(entry_type.pk),
            )
    for kind, field_key, code, name, entries in STARTER_LISTS:
        existing = LookupList.objects.filter(
            site=site, module="addressbook", field_key=field_key
        ).first()
        if existing is None:
            lookup, created = LookupList.objects.get_or_create(
                site=site,
                module="addressbook",
                kind=kind,
                code=code,
                defaults={"field_key": field_key, "name": name},
            )
        else:
            lookup, created = existing, False
        if lookup.kind != kind or lookup.field_key != field_key:
            raise ValueError(f"Le référentiel {code!r} du site {site.pk} cible un autre champ.")
        if created:
            created_count += 1
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="setup.addressbook.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(lookup.pk),
            )
        # A custom list retains its own vocabulary. Only starter lists get
        # starter values; existing labels and active flags are never replaced.
        if lookup.code != code:
            continue
        for value, label, addressbook_kind, *extra in entries:
            item, created = LookupEntry.objects.get_or_create(
                lookup_list=lookup,
                value=value,
                defaults={
                    "label": label,
                    "addressbook_kind": addressbook_kind,
                    "value_kind": extra[0] if extra else "",
                },
            )
            if created:
                created_count += 1
                AuditEvent.objects.create(
                    actor=actor,
                    site=site,
                    action="setup.addressbook.reference_entry.create",
                    entity_type="configuration.lookupentry",
                    entity_id=str(item.pk),
                )
    return created_count
