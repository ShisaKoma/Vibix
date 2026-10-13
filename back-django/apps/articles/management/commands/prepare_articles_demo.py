from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import SiteMembership

from ...models import Article


class Command(BaseCommand):
    help = "Prépare uniquement le site local demo et son rôle demo-site-admin pour Articles."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Cette commande exige DEBUG=True et une affectation locale existante."
            )
        membership = (
            SiteMembership.objects.select_related("site", "role", "user")
            .filter(
                user__username="demo",
                user__is_active=True,
                site__code="demo",
                site__is_active=True,
                role__code="demo-site-admin",
                is_active=True,
            )
            .first()
        )
        if not membership or membership.site_id == 1:
            raise CommandError("Le site et l’affectation de démonstration doivent déjà exister.")
        site, role = membership.site, membership.role
        if SiteMembership.objects.filter(role=role).exclude(site=site).exists():
            raise CommandError("Le rôle de démonstration est utilisé sur un autre site.")
        if not role.permissions.filter(
            content_type__app_label="tenancy", codename="manage_site_settings"
        ).exists():
            raise CommandError("Le rôle doit déjà être administrateur de ce site.")
        permissions = Permission.objects.filter(
            content_type__app_label="articles", content_type__model="article"
        )
        if permissions.count() != 6:
            raise CommandError("Appliquez les migrations avant de préparer Articles.")
        if role.permissions.filter(pk__in=permissions).count() != 6:
            role.permissions.add(*permissions)
            AuditEvent.objects.create(
                site=site,
                action="demo.articles.permissions",
                entity_type="tenancy.role",
                entity_id=str(role.pk),
            )
        categories, created = LookupList.objects.get_or_create(
            site=site,
            module="articles",
            kind="category",
            field_key="articles_article.category",
            code="demo-articles",
            defaults={"name": "Catégories Articles"},
        )
        self.record(categories, created, site, "lookuplist")
        root, created = LookupEntry.objects.get_or_create(
            lookup_list=categories, value="actualites", defaults={"label": "Actualités"}
        )
        self.record(root, created, site, "lookupentry")
        child, created = LookupEntry.objects.get_or_create(
            lookup_list=categories,
            value="vie-locale",
            defaults={"label": "Vie locale", "parent": root},
        )
        self.record(child, created, site, "lookupentry")
        languages, created = LookupList.objects.get_or_create(
            site=site,
            module="articles",
            kind="choice",
            field_key="articles_article.language",
            code="demo-articles-langues",
            defaults={"name": "Langues des articles"},
        )
        self.record(languages, created, site, "lookuplist")
        french = None
        for code, label in (("fr", "Français"), ("en", "English")):
            language, created = LookupEntry.objects.get_or_create(
                lookup_list=languages, value=code, defaults={"label": label}
            )
            self.record(language, created, site, "lookupentry")
            if code == "fr":
                french = language
        for reference, title, category, position in (
            ("ART-DEMO-01", "Bienvenue dans le module Articles", root, 10),
            ("ART-DEMO-02", "Les nouvelles de la vie locale", child, 5),
        ):
            article, created = Article.objects.get_or_create(
                site=site,
                reference=reference,
                defaults={
                    "title": title,
                    "category": category,
                    "language": french,
                    "position": position,
                    "content": (
                        "<p>Article fictif pour valider les parcours de rédaction "
                        "et de publication.</p>"
                    ),
                    "created_by": membership.user,
                    "updated_by": membership.user,
                },
            )
            if created:
                AuditEvent.objects.create(
                    site=site,
                    action="articles.article.create",
                    entity_type="articles.article",
                    entity_id=str(article.pk),
                )
        self.stdout.write(
            self.style.SUCCESS(
                "Articles est prêt sur demo : catégories, langues et deux brouillons fictifs."
            )
        )

    def record(self, instance, created, site, kind):
        if created:
            AuditEvent.objects.create(
                site=site,
                action="settings.reference.create",
                entity_type=f"configuration.{kind}",
                entity_id=str(instance.pk),
            )
