from django.db import migrations


EDITORIAL_LISTS = (
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


def seed_references(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    LookupList = apps.get_model("configuration", "LookupList")
    LookupEntry = apps.get_model("configuration", "LookupEntry")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    for site in Site.objects.using(alias).exclude(pk=1).order_by("pk").iterator():
        for module, kind, field, code, name, values in EDITORIAL_LISTS:
            lookup, created = LookupList.objects.using(alias).get_or_create(
                site_id=site.pk,
                module=module,
                kind=kind,
                code=code,
                defaults={"name": name, "field_key": field},
            )
            if lookup.field_key != field:
                raise ValueError(f"Le référentiel {code!r} cible un autre champ.")
            if created:
                AuditEvent.objects.using(alias).create(
                    site_id=site.pk,
                    action="setup.editorial.reference.create",
                    entity_type="configuration.lookuplist",
                    entity_id=str(lookup.pk),
                )
            for value, label in values:
                entry, created = LookupEntry.objects.using(alias).get_or_create(
                    lookup_list_id=lookup.pk,
                    value=value,
                    defaults={"label": label},
                )
                if created:
                    AuditEvent.objects.using(alias).create(
                        site_id=site.pk,
                        action="setup.editorial.reference_entry.create",
                        entity_type="configuration.lookupentry",
                        entity_id=str(entry.pk),
                    )


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0007_alter_lookuplist_field_key"),
        ("tenancy", "0002_reserve_owner_site"),
        ("audit", "0002_initial"),
    ]
    # References may already be used by customer content: do not delete them on reverse.
    operations = [migrations.RunPython(seed_references, migrations.RunPython.noop)]
