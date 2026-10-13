import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from apps.core.phpboost_import import PhpboostPackage, import_package
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Import PHPBoost pilote, en brouillon, uniquement sur le site demo en développement."

    def add_arguments(self, parser):
        parser.add_argument("--package", required=True)
        parser.add_argument("--site-code", required=True, choices=["demo"])
        parser.add_argument("--actor", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--allow-review-drafts", action="store_true")

    def handle(self, *args, **options):
        site = Site.objects.filter(code=options["site_code"]).first()
        actor = get_user_model().objects.filter(username=options["actor"], is_active=True).first()
        if site is None or actor is None:
            raise CommandError("Site ou acteur actif introuvable ; aucun compte/site n’est créé.")
        try:
            package = PhpboostPackage(options["package"])
            result = import_package(
                package,
                site,
                actor,
                apply=options["apply"],
                allow_review=options["allow_review_drafts"],
            )
        except (OSError, ValueError, KeyError, TypeError, IntegrityError) as error:
            raise CommandError(
                "Paquet invalide ou conflit en base ; aucune écriture conservée."
            ) from error
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
