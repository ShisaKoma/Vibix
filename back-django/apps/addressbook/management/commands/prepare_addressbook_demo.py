"""Grant the private addressbook to the existing, isolated local demo role."""

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.tenancy.models import SiteMembership

ENTRY_PERMISSIONS = {"view_entry", "add_entry", "change_entry", "delete_entry", "restore_entry"}


class Command(BaseCommand):
    help = "Autorise le carnet privé pour le rôle local demo-site-admin, sans créer de fiches."

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
            raise CommandError("Le rôle doit déjà être administrateur du site demo.")
        permissions = list(
            Permission.objects.filter(
                content_type__app_label="addressbook",
                content_type__model="entry",
                codename__in=ENTRY_PERMISSIONS,
            )
        )
        if {permission.codename for permission in permissions} != ENTRY_PERMISSIONS:
            raise CommandError("Appliquez les migrations avant de préparer Addressbook.")
        existing = set(
            role.permissions.filter(pk__in=[p.pk for p in permissions]).values_list("pk", flat=True)
        )
        missing = [permission for permission in permissions if permission.pk not in existing]
        if missing:
            role.permissions.add(*missing)
            AuditEvent.objects.create(
                actor=membership.user,
                site=site,
                action="demo.addressbook.permissions",
                entity_type="tenancy.role",
                entity_id=str(role.pk),
            )
            self.stdout.write(self.style.SUCCESS("Droits du carnet activés pour demo-site-admin."))
        else:
            self.stdout.write("Droits du carnet déjà présents ; aucune modification.")
