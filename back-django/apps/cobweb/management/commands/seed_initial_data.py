from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.cobweb.seeds import seed_cobweb_references
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Prépare les référentiels Cobweb de base pour un ou plusieurs sites."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--site-code", help="Code exact du site à préparer.")
        target.add_argument(
            "--all-sites",
            action="store_true",
            help="Prépare tous les sites clients actifs et inactifs, sauf le site réservé 1.",
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

        prepared = 0
        for site in sites:
            created = seed_cobweb_references(site)
            prepared += created
            self.stdout.write(f"{site.code}: {created} référence(s) créée(s).")
        self.stdout.write(
            self.style.SUCCESS(
                f"Référentiels Cobweb prêts ; {prepared} liste(s) ou valeur(s) créée(s)."
            )
        )
