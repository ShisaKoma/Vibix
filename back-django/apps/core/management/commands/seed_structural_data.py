from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.addressbook.seeds import seed_addressbook_references
from apps.cobweb.seeds import seed_cobweb_references
from apps.configuration.seeds import seed_client_references, seed_editorial_references
from apps.contacts.seeds import seed_contact_forms
from apps.tenancy.constants import OWNER_SITE_ID
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Initialise les données structurelles des sites, sans contenu fictif ni permissions."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--site-code")
        target.add_argument("--all-sites", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        sites = Site.objects.select_for_update().exclude(pk=OWNER_SITE_ID).order_by("pk")
        if not options["all_sites"]:
            sites = sites.filter(code=options["site_code"])
            if not sites.exists():
                raise CommandError("Site client introuvable ; le site réservé 1 est exclu.")
        total = 0
        try:
            for site in sites:
                count = sum(
                    seed(site)
                    for seed in (
                        seed_editorial_references,
                        seed_client_references,
                        seed_cobweb_references,
                        seed_addressbook_references,
                        seed_contact_forms,
                    )
                )
                total += count
                self.stdout.write(f"{site.code} : {count} référence(s) créée(s).")
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Structure prête : {total} référence(s) créée(s)."))
