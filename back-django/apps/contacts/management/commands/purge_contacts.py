from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.contacts.models import ContactRequest, QuotaBucket
from apps.contacts.security import policy_for
from apps.tenancy.models import Site


class Command(BaseCommand):
    help = (
        "Aperçu ou purge des demandes selon la rétention du site ; --confirm requis pour effacer."
    )

    def add_arguments(self, parser):
        parser.add_argument("--site-code", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        site = Site.objects.exclude(pk=1).filter(code=options["site_code"]).first()
        if not site:
            raise CommandError("Site client introuvable.")
        cutoff = timezone.now() - timedelta(days=policy_for(site).contact_retention_days)
        contacts = ContactRequest.objects.filter(site=site, created_at__lt=cutoff)
        self.stdout.write(f"Demandes arrivées à échéance : {contacts.count()}.")
        if not options["confirm"]:
            return
        while True:
            # Short batches allow reception and carnet actions between transactions.
            with transaction.atomic():
                Site.objects.select_for_update(no_key=True).get(pk=site.pk)
                batch = list(contacts.order_by("pk").values_list("pk", flat=True)[:500])
                if not batch:
                    break
                AuditEvent.objects.bulk_create(
                    [
                        AuditEvent(
                            site=site,
                            action="contacts.retention.purge",
                            entity_type="contacts.contactrequest",
                            entity_id=str(pk),
                        )
                        for pk in batch
                    ]
                )
                ContactRequest.objects.filter(pk__in=batch, site=site).delete()
        QuotaBucket.objects.filter(
            site=site, hour__lt=timezone.now() - timedelta(hours=48)
        ).delete()
