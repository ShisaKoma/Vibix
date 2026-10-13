from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def initialize_existing_logins(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    db = schema_editor.connection.alias
    profiles = [
        UserProfile(user_id=user.pk, first_run_completed_at=user.last_login)
        for user in User.objects.using(db).only("pk", "last_login").iterator()
        if user.last_login is not None
    ]
    UserProfile.objects.using(db).bulk_create(profiles, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("tenancy", "0003_alter_site_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_run_completed_at", models.DateTimeField(blank=True, null=True, verbose_name="fin du premier démarrage")),
                ("preferred_site", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="preferred_by", to="tenancy.site", verbose_name="site de démarrage")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "profil utilisateur"},
        ),
        migrations.RunPython(initialize_existing_logins, migrations.RunPython.noop),
    ]
