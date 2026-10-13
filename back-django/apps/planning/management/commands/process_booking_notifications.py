import time

from django.core.management.base import BaseCommand, CommandError

from apps.planning.booking_notifications import claim, deliver
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Traite la file SMTP Réservations interne, indépendamment de Contacts."

    def add_arguments(self, parser):
        parser.add_argument("--site-code")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("La limite doit être entre 1 et 1000.")
        site_id = None
        if options["site_code"]:
            site_id = (
                Site.objects.exclude(pk=1)
                .filter(code=options["site_code"])
                .values_list("pk", flat=True)
                .first()
            )
            if site_id is None:
                raise CommandError("Site client introuvable.")
        while True:
            outcomes = {}
            for _ in range(options["limit"]):
                job = claim(site_id)
                if not job:
                    break
                state = deliver(job)
                outcomes[state] = outcomes.get(state, 0) + 1
            if outcomes:
                self.stdout.write(f"Notifications Réservations : {outcomes}")
            if not options["loop"]:
                break
            time.sleep(5)
