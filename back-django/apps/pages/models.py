from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import PublicationFields


class Page(PublicationFields):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT, related_name="pages")
    category = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        verbose_name="catégorie",
        related_name="pages",
    )
    additional_categories = models.ManyToManyField(
        "configuration.LookupEntry",
        blank=True,
        related_name="additional_pages",
        verbose_name="catégories complémentaires",
    )
    language = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="language_pages",
        verbose_name="langue",
    )
    group = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="group_pages",
        verbose_name="groupe",
    )
    url_language = models.CharField(max_length=64, default="default", editable=False)
    slug = models.SlugField("adresse de la page", max_length=160)
    title = models.CharField("titre", max_length=200)
    subtitle = models.CharField("sous-titre / courte description", max_length=300, blank=True)
    reference = models.CharField("référence", max_length=64, blank=True)
    content = models.TextField("description", max_length=100000, blank=True)
    position = models.PositiveIntegerField("priorité d’affichage", default=0)
    is_active = models.BooleanField("actif dans le back-office", default=True)
    published_at = models.DateTimeField(
        "première autorisation de publication", null=True, editable=False
    )
    deleted_at = models.DateTimeField("mise en corbeille", null=True, editable=False)
    seo_title = models.CharField("titre SEO", max_length=200, blank=True)
    seo_description = models.CharField("description SEO", max_length=300, blank=True)
    seo_keywords = models.CharField("mots-clés SEO", max_length=300, blank=True)
    created_at = models.DateTimeField("création", auto_now_add=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="authored_pages",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="edited_pages",
    )

    class Meta:
        ordering = ["-position", "-created_at", "pk"]
        permissions = [
            ("publish_page", "Peut publier les pages"),
            ("restore_page", "Peut restaurer les pages de la corbeille"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "url_language", "slug"], name="unique_page_url"
            ),
        ]
        indexes = [models.Index(fields=["site", "deleted_at", "is_online", "position"])]
        verbose_name = "page"
        verbose_name_plural = "pages"

    def clean(self):
        super().clean()
        self.slug = self.slug.lower()
        self.url_language = self.language.value if self.language_id else "default"
        for name, kind, key in (
            ("category", "category", "pages_page.category"),
            ("language", "choice", "pages_page.language"),
            ("group", "category", "pages_page.group"),
        ):
            if getattr(self, f"{name}_id"):
                lookup = getattr(self, name).lookup_list
                if (
                    lookup.site_id != self.site_id
                    or lookup.module != "pages"
                    or lookup.kind != kind
                    or lookup.field_key != key
                ):
                    raise ValidationError(
                        {name: "Choisissez une valeur Pages du même site et du champ ciblé."}
                    )

    def save(self, *args, **kwargs):
        fields = kwargs.get("update_fields")
        if fields is None or {"slug", "language", "language_id", "url_language"}.intersection(
            fields
        ):
            self.slug = self.slug.lower()
            self.url_language = self.language.value if self.language_id else "default"
            if fields is not None:
                kwargs["update_fields"] = set(fields) | {"slug", "url_language"}
        return super().save(*args, **kwargs)

    @property
    def is_in_publication_window(self):
        return self.deleted_at is None and super().is_in_publication_window

    def __str__(self):
        return self.title


class PageImage(models.Model):
    """Gallery positions 2–4; Page.picture holds historical picture01."""

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="gallery")
    asset = models.ForeignKey("media.Asset", on_delete=models.PROTECT, verbose_name="image")
    position = models.PositiveSmallIntegerField(
        "position dans la galerie", validators=[MinValueValidator(2), MaxValueValidator(4)]
    )

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "position"], name="unique_page_gallery_position"
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=2, position__lte=4),
                name="page_gallery_position_range",
            ),
        ]

    def clean(self):
        super().clean()
        if self.page_id and self.asset_id and self.page.site_id != self.asset.site_id:
            raise ValidationError({"asset": "L’image doit appartenir au site de la page."})
