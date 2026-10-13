"""Structural editorial references, without demonstration content."""

from django.db import transaction

from apps.audit.models import AuditEvent
from apps.tenancy.constants import OWNER_SITE_ID

from .models import LookupEntry, LookupList

EDITORIAL_LISTS = (
    (
        "products",
        "category",
        "products_product.category",
        "starter-products-categories",
        "Catégories Produits",
        (),
    ),
    (
        "products",
        "choice",
        "products_product.language",
        "starter-products-languages",
        "Langues des produits",
        (("fr", "Français"), ("en", "English")),
    ),
    (
        "contacts",
        "category",
        "contacts_contactrequest.category",
        "starter-contacts-categories",
        "Catégories Contacts",
        (),
    ),
    (
        "articles",
        "category",
        "articles_article.category",
        "starter-articles-categories",
        "Catégories Articles",
        (("general", "Général"),),
    ),
    (
        "articles",
        "choice",
        "articles_article.language",
        "starter-articles-languages",
        "Langues des articles",
        (("fr", "Français"), ("en", "English")),
    ),
    (
        "pages",
        "category",
        "pages_page.category",
        "starter-pages-categories",
        "Catégories Pages",
        (("general", "Général"),),
    ),
    (
        "pages",
        "choice",
        "pages_page.language",
        "starter-pages-languages",
        "Langues des pages",
        (("fr", "Français"), ("en", "English")),
    ),
    ("pages", "category", "pages_page.group", "starter-pages-groups", "Groupes de pages", ()),
)


@transaction.atomic
def seed_editorial_references(site, *, actor=None):
    if site.pk == OWNER_SITE_ID:
        return 0
    count = 0
    for module, kind, field, code, name, values in EDITORIAL_LISTS:
        lookup, created = LookupList.objects.get_or_create(
            site=site,
            module=module,
            kind=kind,
            code=code,
            defaults={"name": name, "field_key": field},
        )
        if lookup.field_key != field:
            raise ValueError(f"Le référentiel {code!r} cible un autre champ.")
        if created:
            count += 1
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="setup.editorial.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(lookup.pk),
            )
        for value, label in values:
            entry, created = LookupEntry.objects.get_or_create(
                lookup_list=lookup,
                value=value,
                defaults={"label": label},
            )
            if created:
                count += 1
                AuditEvent.objects.create(
                    actor=actor,
                    site=site,
                    action="setup.editorial.reference_entry.create",
                    entity_type="configuration.lookupentry",
                    entity_id=str(entry.pk),
                )
    return count


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


@transaction.atomic
def seed_client_references(site, *, actor=None):
    """Front capabilities, without changing CMS appearance or site preferences."""
    if site.pk == OWNER_SITE_ID:
        return 0
    count = 0
    for field, code, name, values in CLIENT_LISTS:
        lookup, created = LookupList.objects.get_or_create(
            site=site,
            module="api",
            kind="choice",
            code=code,
            defaults={"name": name, "field_key": field},
        )
        if lookup.field_key != field:
            raise ValueError(f"Le référentiel {code!r} cible un autre champ.")
        if created:
            count += 1
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="setup.client.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(lookup.pk),
            )
        for position, (value, label) in enumerate(values):
            entry, created = LookupEntry.objects.get_or_create(
                lookup_list=lookup,
                value=value,
                defaults={"label": label, "position": position},
            )
            if created:
                count += 1
                AuditEvent.objects.create(
                    actor=actor,
                    site=site,
                    action="setup.client.reference_entry.create",
                    entity_type="configuration.lookupentry",
                    entity_id=str(entry.pk),
                )
    return count
