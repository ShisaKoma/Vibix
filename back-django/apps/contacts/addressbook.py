from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.addressbook.models import ContactPoint, Entry, EntryType
from apps.audit.models import AuditEvent
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .models import ContactRequest
from .security import policy_for


class StaleLink(Exception):
    pass


def candidates(contact):
    return Entry.objects.filter(
        site=contact.site,
        kind="person",
        is_active=True,
        archived_at__isnull=True,
        contacts__mode__in=["email", "work_email"],
        contacts__value__iexact=contact.email,
    ).distinct()


def require_permission(user, site, code):
    if not has_site_permission(user, site, code):
        raise PermissionDenied


@transaction.atomic
def link_contact(user, contact_id, site_id, data):
    site = Site.objects.select_for_update(no_key=True).get(pk=site_id)
    require_permission(user, site, "contacts.view_contactrequest")
    require_permission(user, site, "contacts.link_addressbook")
    require_permission(user, site, "addressbook.view_entry")
    contact = ContactRequest.objects.select_for_update().get(pk=contact_id, site=site)
    if contact.updated_at.isoformat() != data["version"] or contact.addressbook_entry_id:
        raise StaleLink
    if contact.status not in {"accepted", "in_progress", "closed"} or contact.deleted_at:
        raise ValidationError("Validez la demande avant de la lier au carnet.")
    mode = data["mode"]
    if mode not in {"create", "link", "sync"} or not data.get("confirm"):
        raise ValidationError("Confirmez une action valide avant de modifier le carnet.")
    if mode == "create":
        require_permission(user, site, "addressbook.add_entry")
        if candidates(contact).exists():
            raise ValidationError(
                "Une personne possède déjà ce courriel. "
                "Examinez les correspondances avant de l’associer."
            )
        try:
            entry_type = EntryType.objects.select_for_update().get(
                pk=data["entry_type"].pk, site=site, kind="person", is_active=True
            )
        except EntryType.DoesNotExist as error:
            raise ValidationError(
                "Le type de personne n’est plus disponible. Rechargez l’aperçu."
            ) from error
        entry = Entry(
            site=site,
            kind="person",
            entry_type=entry_type,
            name=contact.name,
            first_name=contact.first_name,
            created_by=user,
            updated_by=user,
        )
        entry.full_clean()
        entry.save()
    else:
        entry = Entry.objects.select_for_update().get(
            pk=data["entry"].pk,
            site=site,
            kind="person",
            is_active=True,
            archived_at__isnull=True,
        )
        if entry.updated_at.isoformat() != data["entry_version"]:
            raise StaleLink
        if mode == "sync":
            require_permission(user, site, "addressbook.change_entry")
            if policy_for(site).contact_addressbook_mode == "create":
                raise ValidationError(
                    "La politique du site propose uniquement l’ajout ou l’association."
                )
            if data.get("update_name"):
                entry.name, entry.first_name = contact.name, contact.first_name
            entry.updated_by = user
            entry.full_clean()
            entry.save()
    if mode in {"create", "sync"}:
        for field, contact_mode in (("email", "email"), ("phone", "phone")):
            value = getattr(contact, field)
            if not value or mode == "sync" and not data.get(f"add_{field}"):
                continue
            if entry.contacts.filter(mode=contact_mode, value__iexact=value).exists():
                continue
            point = ContactPoint(
                entry=entry,
                mode=contact_mode,
                value=value,
                is_primary=not entry.contacts.filter(mode=contact_mode, is_primary=True).exists(),
            )
            point.full_clean()
            point.save()
    contact.addressbook_entry = entry
    contact.full_clean()
    contact.save()
    AuditEvent.objects.create(
        actor=user,
        site=site,
        action=f"contacts.addressbook.{mode}",
        entity_type="contacts.contactrequest",
        entity_id=str(contact.pk),
    )
    if mode in {"create", "sync"}:
        AuditEvent.objects.create(
            actor=user,
            site=site,
            action=f"addressbook.contacts.{mode}",
            entity_type="addressbook.entry",
            entity_id=str(entry.pk),
        )
    return entry
