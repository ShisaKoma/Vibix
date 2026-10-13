from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Global identity; site permissions live in tenancy, not user.groups."""

    class Meta(AbstractUser.Meta):
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    preferred_site = models.ForeignKey(
        "tenancy.Site",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="preferred_by",
        verbose_name="site de démarrage",
    )
    first_run_completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="fin du premier démarrage"
    )

    class Meta:
        verbose_name = "profil utilisateur"

    def __str__(self):
        return f"Préférences de {self.user}"
