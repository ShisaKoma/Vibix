from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.cobweb.content import rendered_content


class PublicationFields(models.Model):
    """Shared publication and protected media fields for site-local content."""

    is_online = models.BooleanField("visible en ligne", default=False)
    is_searchable = models.BooleanField("autoriser l’indexation", default=True)
    starts_at = models.DateTimeField("visible à partir du", null=True, blank=True)
    ends_at = models.DateTimeField("visible jusqu’au", null=True, blank=True)
    picture = models.ForeignKey(
        "media.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="%(class)s_pictures",
        verbose_name="visuel intérieur",
    )
    thumbnail = models.ForeignKey(
        "media.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="%(class)s_thumbnails",
        verbose_name="visuel de liste",
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": "La fin doit être postérieure au début."})
        for field in ("picture", "thumbnail"):
            asset = getattr(self, field)
            if asset and asset.site_id != self.site_id:
                raise ValidationError({field: "Le média doit appartenir au site de la fiche."})

    @property
    def rendered_content(self):
        return rendered_content(self.content)

    @property
    def is_in_publication_window(self):
        now = timezone.now()
        return (
            self.is_active
            and self.is_online
            and (not self.starts_at or self.starts_at <= now)
            and (not self.ends_at or self.ends_at > now)
        )


class SeedRecord(models.Model):
    """Stable identities for opt-in, versioned datasets, independent of audit retention."""

    site = models.ForeignKey("tenancy.Site", on_delete=models.CASCADE)
    dataset = models.SlugField(max_length=64)
    key = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "dataset", "key"], name="unique_site_seed_key")
        ]


class ImportedRecord(models.Model):
    """Source identities and receipts for the explicit development import command."""

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    source = models.SlugField(max_length=48)
    key = models.CharField(max_length=200)
    entity_type = models.CharField(max_length=100)
    entity_id = models.PositiveBigIntegerField()
    source_checksum = models.CharField(max_length=64)
    target_checksum = models.CharField(max_length=64)
    package_checksum = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "source", "key"], name="unique_site_import_source_key"
            )
        ]
