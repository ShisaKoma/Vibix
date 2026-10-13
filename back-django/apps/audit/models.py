from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="acteur",
    )
    site = models.ForeignKey(
        "tenancy.Site", null=True, blank=True, on_delete=models.SET_NULL, verbose_name="site"
    )
    action = models.CharField("action", max_length=100)
    entity_type = models.CharField("type d’objet", max_length=100, blank=True)
    entity_id = models.CharField("identifiant", max_length=64, blank=True)
    occurred_at = models.DateTimeField("date", default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        verbose_name = "événement d’audit"
        verbose_name_plural = "événements d’audit"

    def __str__(self):
        return f"{self.occurred_at} — {self.action}"
