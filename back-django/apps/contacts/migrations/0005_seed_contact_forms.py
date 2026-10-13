from django.db import migrations


STARTER_FORMS = (("web-contact", "Contact web"), ("contact", "Contact"), ("devis", "Demande de devis"), ("recrutement", "Recrutement"))


def initialize(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    Definition = apps.get_model("contacts", "ContactFormDefinition")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    alias = schema_editor.connection.alias
    for site in Site.objects.using(alias).exclude(pk=1).iterator():
        for position, (code, label) in enumerate(STARTER_FORMS):
            definition, created = Definition.objects.using(alias).get_or_create(site_id=site.pk, code=code, defaults={"label": label, "position": position})
            if created:
                AuditEvent.objects.using(alias).create(site_id=site.pk, action="setup.contacts.form.create", entity_type="contacts.contactformdefinition", entity_id=str(definition.pk))


class Migration(migrations.Migration):
    dependencies = [
        ("contacts", "0004_contactrequest_answers_contactrequest_form_snapshot_and_more"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(initialize, migrations.RunPython.noop)]
