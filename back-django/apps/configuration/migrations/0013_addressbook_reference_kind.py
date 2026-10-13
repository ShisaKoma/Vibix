from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("configuration", "0012_contact_retention_days")]

    operations = [
        migrations.AddField(
            model_name="lookupentry",
            name="addressbook_kind",
            field=models.CharField(
                "famille du carnet",
                max_length=16,
                blank=True,
                choices=[
                    ("", "Toutes les familles"),
                    ("person", "Personne"),
                    ("company", "Entreprise"),
                    ("association", "Association"),
                ],
                default="",
                help_text="Pour le carnet uniquement ; vide autorise toutes les familles.",
            ),
        ),
        migrations.AddConstraint(
            model_name="lookupentry",
            constraint=models.CheckConstraint(
                condition=models.Q(addressbook_kind__in=["", "person", "company", "association"]),
                name="config_lookup_addressbook_kind",
            ),
        ),
    ]
