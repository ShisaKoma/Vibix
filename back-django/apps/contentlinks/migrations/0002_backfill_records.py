from django.db import migrations


def backfill(apps, schema_editor):
    records = apps.get_model("contentlinks", "ContentRecord")
    alias = schema_editor.connection.alias
    for model_name, field in (("CobwebItem", "item_id"), ("CobwebGroup", "group_id")):
        model = apps.get_model("cobweb", model_name)
        for pk, site_id in model.objects.using(alias).values_list("pk", "site_id").iterator():
            records.objects.using(alias).get_or_create(**{field: pk}, defaults={"site_id": site_id})


class Migration(migrations.Migration):
    dependencies = [("contentlinks", "0001_initial"), ("cobweb", "0004_cobwebgroup_picture_cobwebgroup_presentation_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
