from django.db import transaction

from apps.audit.models import AuditEvent
from apps.tenancy.constants import OWNER_SITE_ID

from .models import ContactFormDefinition

STARTER_FORMS = (
    ("web-contact", "Contact web"),
    ("contact", "Contact"),
    ("devis", "Demande de devis"),
    ("recrutement", "Recrutement"),
)


@transaction.atomic
def seed_contact_forms(site, *, actor=None):
    if site.pk == OWNER_SITE_ID:
        return 0
    count = 0
    for position, (code, label) in enumerate(STARTER_FORMS):
        definition, created = ContactFormDefinition.objects.get_or_create(
            site=site, code=code, defaults={"label": label, "position": position}
        )
        if created:
            count += 1
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="setup.contacts.form.create",
                entity_type="contacts.contactformdefinition",
                entity_id=str(definition.pk),
            )
    return count
