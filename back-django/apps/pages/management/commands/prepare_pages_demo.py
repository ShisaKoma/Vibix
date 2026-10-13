from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import SiteMembership

from ...models import Page


class Command(BaseCommand):
    help = "Prépare uniquement le site local demo et son rôle demo-site-admin pour Pages."

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
            content_type__app_label="pages", content_type__model="page"
        )
        if permissions.count() != 6:
            raise CommandError("Appliquez les migrations avant de préparer Pages.")
        if role.permissions.filter(pk__in=permissions).count() != 6:
            role.permissions.add(*permissions)
            AuditEvent.objects.create(
                site=site,
                action="demo.pages.permissions",
                entity_type="tenancy.role",
                entity_id=str(role.pk),
            )
        categories, created = LookupList.objects.get_or_create(
            site=site,
            module="pages",
            kind="category",
            field_key="pages_page.category",
            code="demo-pages",
            defaults={"name": "Catégories Pages"},
        )
        self.record(categories, created, site, "lookuplist")
        root, created = LookupEntry.objects.get_or_create(
            lookup_list=categories, value="institutionnel", defaults={"label": "Institutionnel"}
        )
        self.record(root, created, site, "lookupentry")
        child, created = LookupEntry.objects.get_or_create(
            lookup_list=categories,
            value="informations",
            defaults={"label": "Informations", "parent": root},
        )
        self.record(child, created, site, "lookupentry")
        languages, created = LookupList.objects.get_or_create(
            site=site,
            module="pages",
            kind="choice",
            field_key="pages_page.language",
            code="demo-pages-langues",
            defaults={"name": "Langues des pages"},
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
        groups, created = LookupList.objects.get_or_create(
            site=site,
            module="pages",
            kind="category",
            field_key="pages_page.group",
            code="demo-pages-groupes",
            defaults={"name": "Groupes Pages"},
        )
        self.record(groups, created, site, "lookuplist")
        navigation, created = LookupEntry.objects.get_or_create(
            lookup_list=groups,
            value="navigation",
            defaults={"label": "Navigation du site"},
        )
        self.record(navigation, created, site, "lookupentry")
        practical, created = LookupEntry.objects.get_or_create(
            lookup_list=groups,
            value="pratique",
            defaults={"label": "Informations pratiques", "parent": navigation},
        )
        self.record(practical, created, site, "lookupentry")
        for reference, slug, title, category, position in (
            ("PAGE-DEMO-01", "presentation", "Présentation du site", root, 10),
            ("PAGE-DEMO-02", "informations-pratiques", "Informations pratiques", child, 5),
        ):
            page, created = Page.objects.get_or_create(
                site=site,
                reference=reference,
                defaults={
                    "title": title,
                    "slug": slug,
                    "group": practical,
                    "category": category,
                    "language": french,
                    "position": position,
                    "content": (
                        "<p>Page fictive pour valider les parcours de rédaction "
                        "et de publication.</p>"
                    ),
                    "created_by": membership.user,
                    "updated_by": membership.user,
                },
            )
            if created:
                AuditEvent.objects.create(
                    site=site,
                    action="pages.page.create",
                    entity_type="pages.page",
                    entity_id=str(page.pk),
                )
        self.stdout.write(
            self.style.SUCCESS(
                "Pages est prêt sur demo : catégories, langues, groupes et deux brouillons fictifs."
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
