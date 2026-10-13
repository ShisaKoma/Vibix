from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.addressbook.seeds import seed_addressbook_references
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Prépare les types et listes de référence du carnet pour un ou plusieurs sites."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--site-code", help="Code exact du site à préparer.")
        target.add_argument(
            "--all-sites", action="store_true", help="Tous les sites sauf le site réservé 1."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["all_sites"]:
            sites = Site.objects.exclude(pk=OWNER_SITE_ID).order_by("pk")
        else:
            try:
                sites = [Site.objects.get(code=options["site_code"])]
            except Site.DoesNotExist as exc:
                raise CommandError("Aucun site ne correspond à ce code.") from exc
            if sites[0].pk == OWNER_SITE_ID:
                raise CommandError("Le site réservé 1 ne reçoit pas de données métier.")
        total = 0
        for site in sites:
            count = seed_addressbook_references(site)
            total += count
            self.stdout.write(f"{site.code}: {count} type(s) ou valeur(s) créée(s).")
        self.stdout.write(self.style.SUCCESS(f"Carnet prêt : {total} référence(s) créée(s)."))
