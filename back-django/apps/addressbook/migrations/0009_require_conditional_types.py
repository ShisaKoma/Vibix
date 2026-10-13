"""Require the new references only after every historical row has been backfilled."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("addressbook", "0008_backfill_conditional_types")]
    operations = [
        migrations.RemoveField(model_name="address", name="kind"),
        migrations.RemoveField(model_name="extrainfo", name="kind"),
        migrations.AlterField(
            model_name="address",
            name="address_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="addressbook_addresses",
                to="configuration.lookupentry",
                verbose_name="type d’adresse",
            ),
        ),
        migrations.AlterField(
            model_name="extrainfo",
            name="info_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="addressbook_extra_info",
                to="configuration.lookupentry",
                verbose_name="catégorie d’information",
            ),
        ),
    ]
