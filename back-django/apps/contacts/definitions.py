"""Site-local form schemas and receipt snapshots. No writes on public reads."""

from django.http import Http404

from .models import ContactFormDefinition, ContactRequest


def available_forms(site):
    forms = list(ContactFormDefinition.objects.filter(site=site, is_active=True))
    if not forms and not ContactFormDefinition.objects.filter(site=site).exists():
        return [{"code": "web-contact", "label": "Contact", "version": "legacy", "questions": []}]
    return [form.snapshot() for form in forms]


def schema_for(site, code="", *, receipt=None):
    if receipt:
        existing = ContactRequest.objects.filter(site=site, receipt_hash=receipt).first()
        if existing and existing.form_snapshot:
            return existing.form_snapshot
    forms = available_forms(site)
    if not code:
        code = (
            "web-contact"
            if any(form["code"] == "web-contact" for form in forms)
            else (forms[0]["code"] if forms else "")
        )
    for form in forms:
        if form["code"] == code:
            return form
    raise Http404
