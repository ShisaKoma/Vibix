import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("addressbook", "0003_seed_references")]
    operations = [
        migrations.AlterField(
            model_name="entry",
            name="entry_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="entries",
                to="addressbook.entrytype",
                verbose_name="type détaillé",
            ),
        ),
    ]
