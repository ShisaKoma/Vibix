from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import SiteMembership

from ...models import CobwebGroup, CobwebGroupMember, CobwebItem


class Command(BaseCommand):
    help = "Prépare uniquement le site local demo et son rôle demo-site-admin pour Cobweb."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Cette commande est réservée au développement local (DEBUG=True).")
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
        if membership is None or membership.site_id == 1:
            raise CommandError("Le site et l’affectation de démonstration doivent déjà exister.")
        site, role = membership.site, membership.role
        if SiteMembership.objects.filter(role=role).exclude(site=site).exists():
            raise CommandError("Le rôle de démonstration est utilisé sur un autre site.")
        if not role.permissions.filter(
            content_type__app_label="tenancy", codename="manage_site_settings"
        ).exists():
            raise CommandError("Le rôle de démonstration doit déjà être administrateur de ce site.")
        permissions = list(
            Permission.objects.filter(
                content_type__app_label="cobweb",
                content_type__model__in=["cobwebitem", "cobwebgroup"],
            )
        )
        permissions.extend(
            Permission.objects.filter(content_type__app_label="media", content_type__model="asset")
        )
        if len(permissions) != 14:
            raise CommandError("Appliquez les migrations avant de préparer la démonstration.")
        if role.permissions.filter(pk__in=[p.pk for p in permissions]).count() != 14:
            role.permissions.add(*permissions)
            AuditEvent.objects.create(
                site=site,
                action="demo.cobweb.permissions",
                entity_type="tenancy.role",
                entity_id=str(role.pk),
            )
        lookup, created = LookupList.objects.get_or_create(
            site=site,
            module="cobweb",
            kind="category",
            code="demo-cobweb",
            defaults={"name": "Catégories de démonstration"},
        )
        if not lookup.field_key:
            lookup.field_key = "cobweb_item.category"
            lookup.save(update_fields=["field_key"])
        if created:
            AuditEvent.objects.create(
                site=site,
                action="settings.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(lookup.pk),
            )
        categories = {}
        for value, label in (
            ("partners", "Partenaires"),
            ("links", "Liens utiles"),
            ("sitemap", "Plan du site"),
        ):
            category, created = LookupEntry.objects.get_or_create(
                lookup_list=lookup,
                value=value,
                defaults={"label": label},
            )
            categories[value] = category
            if created:
                AuditEvent.objects.create(
                    site=site,
                    action="settings.reference.entry.create",
                    entity_type="configuration.lookupentry",
                    entity_id=str(category.pk),
                )
        group_lookup, group_lookup_created = LookupList.objects.get_or_create(
            site=site,
            module="cobweb",
            kind="category",
            field_key="cobweb_group.category",
            code="demo-cobweb-groups",
            defaults={"name": "Catégories de groupes de démonstration"},
        )
        if group_lookup_created:
            AuditEvent.objects.create(
                site=site,
                action="settings.reference.create",
                entity_type="configuration.lookuplist",
                entity_id=str(group_lookup.pk),
            )
        group_category, group_category_created = LookupEntry.objects.get_or_create(
            lookup_list=group_lookup,
            value="homepage",
            defaults={"label": "Accueil"},
        )
        if group_category_created:
            AuditEvent.objects.create(
                site=site,
                action="settings.reference.entry.create",
                entity_type="configuration.lookupentry",
                entity_id=str(group_category.pk),
            )
        examples = [
            ("COB-DEMO-01", "Atelier Horizon", "partners", True),
            ("COB-DEMO-02", "Ressources et liens utiles", "links", True),
            ("COB-DEMO-03", "Page de présentation", "sitemap", False),
        ]
        for position, (reference, title, category, active) in enumerate(examples, start=1):
            if not CobwebItem.objects.filter(site=site, reference=reference).exists():
                item = CobwebItem(
                    site=site,
                    category=categories[category],
                    reference=reference,
                    title=title,
                    position=position,
                    is_active=active,
                    content="Contenu fictif pour la validation du parcours Cobweb.",
                    link="https://example.com",
                )
                item.full_clean()
                item.save()
                AuditEvent.objects.create(
                    site=site,
                    action="demo.cobweb.item.create",
                    entity_type="cobweb.cobwebitem",
                    entity_id=str(item.pk),
                )
        group, group_created = CobwebGroup.objects.get_or_create(
            site=site,
            title="Groupe de démonstration",
            defaults={
                "category": group_category,
                "position": 1,
                "content": "Composition fictive d’entrées Cobweb pour la validation du parcours.",
            },
        )
        child, child_created = LookupEntry.objects.get_or_create(
            lookup_list=lookup,
            value="local-partners",
            defaults={"label": "Partenaires locaux", "parent": categories["partners"]},
        )
        if child_created:
            AuditEvent.objects.create(
                site=site,
                action="demo.reference.entry.create",
                entity_type="configuration.lookupentry",
                entity_id=str(child.pk),
            )
        for field_key, code, name, entries in (
            (
                "cobweb_item.kind",
                "demo-entry-types",
                "Types d’entrée",
                [("partner", "Partenaire"), ("resource", "Ressource")],
            ),
            (
                "cobweb_group.presentation",
                "demo-presentations",
                "Présentation des groupes",
                [("cards", "Cartes"), ("list", "Liste"), ("slideshow", "Diaporama")],
            ),
        ):
            choice_list, choice_created = LookupList.objects.get_or_create(
                site=site,
                module="cobweb",
                kind="choice",
                code=code,
                defaults={"field_key": field_key, "name": name},
            )
            if choice_created:
                AuditEvent.objects.create(
                    site=site,
                    action="demo.reference.create",
                    entity_type="configuration.lookuplist",
                    entity_id=str(choice_list.pk),
                )
            for value, label in entries:
                entry, entry_created = LookupEntry.objects.get_or_create(
                    lookup_list=choice_list,
                    value=value,
                    defaults={"label": label},
                )
                if entry_created:
                    AuditEvent.objects.create(
                        site=site,
                        action="demo.reference.entry.create",
                        entity_type="configuration.lookupentry",
                        entity_id=str(entry.pk),
                    )
        if group_created:
            demo_items = CobwebItem.objects.filter(site=site, reference__startswith="COB-DEMO-")
            CobwebGroupMember.objects.bulk_create(
                [
                    CobwebGroupMember(group=group, item=item, position=index)
                    for index, item in enumerate(demo_items.order_by("position", "pk"), start=1)
                ]
            )
            AuditEvent.objects.create(
                site=site,
                action="demo.cobweb.group.create",
                entity_type="cobweb.cobwebgroup",
                entity_id=str(group.pk),
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Cobweb est prêt sur le site demo. Les données existantes sont conservées."
            )
        )
