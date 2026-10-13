from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import PublicationFields


class Article(PublicationFields):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT, related_name="articles")
    category = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        verbose_name="catégorie",
        related_name="articles",
    )
    additional_categories = models.ManyToManyField(
        "configuration.LookupEntry",
        blank=True,
        related_name="additional_articles",
        verbose_name="catégories complémentaires",
    )
    language = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="language_articles",
        verbose_name="langue",
    )
    title = models.CharField("titre", max_length=200)
    subtitle = models.CharField("sous-titre / courte description", max_length=300, blank=True)
    reference = models.CharField("référence", max_length=64, blank=True)
    content = models.TextField("description", max_length=100000, blank=True)
    position = models.PositiveIntegerField("priorité d’affichage", default=0)
    in_home = models.BooleanField("afficher en accueil", default=False)
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
        related_name="authored_articles",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="edited_articles",
    )

    class Meta:
        ordering = ["-position", "-created_at", "pk"]
        permissions = [
            ("publish_article", "Peut publier les articles"),
            ("restore_article", "Peut restaurer les articles de la corbeille"),
        ]
        indexes = [models.Index(fields=["site", "deleted_at", "is_online", "position"])]
        verbose_name = "article"
        verbose_name_plural = "articles"

    def clean(self):
        super().clean()
        for name, kind, key in (
            ("category", "category", "articles_article.category"),
            ("language", "choice", "articles_article.language"),
        ):
            if getattr(self, f"{name}_id"):
                lookup = getattr(self, name).lookup_list
                if (
                    lookup.site_id != self.site_id
                    or lookup.module != "articles"
                    or lookup.kind != kind
                    or lookup.field_key != key
                ):
                    raise ValidationError(
                        {name: "Choisissez une valeur Articles du même site et du champ ciblé."}
                    )

    @property
    def is_in_publication_window(self):
        return self.deleted_at is None and super().is_in_publication_window

    def __str__(self):
        return self.title


class ArticleImage(models.Model):
    """Gallery positions 2–8; Article.picture holds historical picture01."""

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="gallery")
    asset = models.ForeignKey("media.Asset", on_delete=models.PROTECT, verbose_name="image")
    position = models.PositiveSmallIntegerField(
        "position dans la galerie", validators=[MinValueValidator(2), MaxValueValidator(8)]
    )

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["article", "position"], name="unique_article_gallery_position"
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=2, position__lte=8),
                name="article_gallery_position_range",
            ),
        ]

    def clean(self):
        super().clean()
        if self.article_id and self.asset_id and self.article.site_id != self.asset.site_id:
            raise ValidationError({"asset": "L’image doit appartenir au site de l’article."})
