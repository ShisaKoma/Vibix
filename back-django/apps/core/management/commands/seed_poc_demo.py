from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.core.poc_seeds import seed_poc_demo
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Jeu POC v1 publié : uniquement DEBUG=True et site demo, sans compte ni permission."

    def add_arguments(self, parser):
        parser.add_argument("--site-code", required=True, choices=["demo"])

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Le jeu POC est interdit hors développement (DEBUG=True requis).")
        site = Site.objects.filter(code=options["site_code"], is_active=True).exclude(pk=1).first()
        if site is None:
            raise CommandError("Le site demo actif doit déjà exister ; aucun site n’est créé.")
        if not all(
            (
                site.public_access_enabled,
                site.articles_enabled,
                site.pages_enabled,
                site.cobweb_enabled,
            )
        ):
            raise CommandError(
                "Le site demo doit déjà autoriser Articles, Pages, Cobweb et le public."
            )
        try:
            counts = seed_poc_demo(site)
        except (ValidationError, ValueError, ObjectDoesNotExist) as error:
            raise CommandError(f"Jeu POC annulé : {error}") from error
        self.stdout.write(
            self.style.SUCCESS(
                f"Jeu POC v1 prêt sur demo : {counts or 'aucun ajout (déjà présent)'}."
            )
        )
