from django.db import transaction

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.constants import OWNER_SITE_ID

STARTER_LISTS = (
    {
        "kind": "category",
        "field_key": "cobweb_item.category",
        "code": "starter-cobweb-items",
        "name": "Fonctions Cobweb",
        "entries": (
            ("medias", "Médias"),
            ("advertisements", "Publicités"),
            ("partners", "Partenaires"),
            ("sitemap", "Sitemap"),
        ),
    },
    {
        "kind": "category",
        "field_key": "cobweb_group.category",
        "code": "starter-cobweb-groups",
        "name": "Compositions Cobweb",
        "entries": (
            ("medias", "Groupes de médias"),
            ("slideshow", "Diaporamas"),
            ("advertisements", "Groupes de publicités"),
            ("partners", "Groupes de partenaires"),
            ("sitemap", "Groupes de sitemap"),
        ),
    },
    {
        "kind": "choice",
        "field_key": "cobweb_item.kind",
        "code": "starter-cobweb-item-types",
        "name": "Types d’entrée Cobweb",
        "entries": (
            ("image", "Image"),
            ("advertisement", "Publicité"),
            ("partner", "Partenaire"),
            ("link", "Lien"),
            ("planning", "Planning"),
        ),
    },
    {
        "kind": "choice",
        "field_key": "cobweb_group.presentation",
        "code": "starter-cobweb-presentations",
        "name": "Présentations Cobweb",
        "entries": (("cards", "Cartes"), ("list", "Liste"), ("slideshow", "Diaporama")),
    },
)


@transaction.atomic
def seed_cobweb_references(site, *, actor=None):
    """Create only reusable Cobweb reference lists for a tenant site."""
    if site.pk == OWNER_SITE_ID:
        return 0

    created_count = 0
    for specification in STARTER_LISTS:
        lookup, created = LookupList.objects.get_or_create(
            site=site,
            module="cobweb",
            kind=specification["kind"],
            code=specification["code"],
            defaults={
                "field_key": specification["field_key"],
                "name": specification["name"],
            },
        )
        if lookup.field_key != specification["field_key"]:
            raise ValueError(
                f"Le référentiel {lookup.code!r} du site {site.pk} cible un autre champ."
            )
        if created:
            created_count += 1
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="setup.cobweb.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(lookup.pk),
            )
        for value, label in specification["entries"]:
            entry, entry_created = LookupEntry.objects.get_or_create(
                lookup_list=lookup,
                value=value,
                defaults={"label": label},
            )
            if entry_created:
                created_count += 1
                AuditEvent.objects.create(
                    actor=actor,
                    site=site,
                    action="setup.cobweb.reference_entry.create",
                    entity_type="configuration.lookupentry",
                    entity_id=str(entry.pk),
                )
    return created_count
