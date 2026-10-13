import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.contacts.models import QuotaBucket
from apps.contacts.notifications import (
    claim_forward,
    claim_notification,
    deliver_forward,
    deliver_notification,
)
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = "Traite la file SMTP Contacts, sans journaliser de coordonnées ou secrets."

    def add_arguments(self, parser):
        parser.add_argument("--site-code")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("La limite doit être comprise entre 1 et 1000.")
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
        last_cleanup = 0
        while True:
            if time.monotonic() - last_cleanup > 300:
                old_buckets = QuotaBucket.objects.filter(
                    hour__lt=timezone.now() - timedelta(hours=48)
                )
                if site_id:
                    old_buckets = old_buckets.filter(site_id=site_id)
                old_buckets.delete()
                last_cleanup = time.monotonic()
            outcomes = {}
            queues = [(claim_notification, deliver_notification), (claim_forward, deliver_forward)]
            for index in range(options["limit"]):
                claim, deliver = queues[index % 2]
                job = claim(site_id)
                if not job:
                    claim, deliver = queues[(index + 1) % 2]
                    job = claim(site_id)
                if not job:
                    break
                outcome = deliver(job)
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcomes:
                self.stdout.write(f"Notifications : {outcomes}")
            if not options["loop"]:
                break
            time.sleep(5)
