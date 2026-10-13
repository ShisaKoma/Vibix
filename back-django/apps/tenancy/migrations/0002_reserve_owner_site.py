from django.core.management.color import no_style
from django.db import migrations


def reserve_owner_site(apps, schema_editor):
    Site = apps.get_model("tenancy", "Site")
    db_alias = schema_editor.connection.alias
    site, created = Site.objects.using(db_alias).get_or_create(
        pk=1, defaults={"code": "owner", "name": "Administration ViBix", "is_active": True}
    )
    if not created and site.code != "owner":
        raise RuntimeError(
            "Le site 1 existe déjà avec un autre usage. Résoudre sa réaffectation avant "
            "de réserver cet identifiant à owner. Aucune donnée n’a été écrasée."
        )
    # Explicitly inserting PK 1 must also advance the PostgreSQL sequence.
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [Site]):
        schema_editor.execute(sql)


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0001_initial")]
    operations = [migrations.RunPython(reserve_owner_site, migrations.RunPython.noop)]
