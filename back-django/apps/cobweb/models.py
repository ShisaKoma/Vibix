from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models

from apps.core.models import PublicationFields


class CobwebItem(PublicationFields):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT, related_name="cobweb_items")
    category = models.ForeignKey(
        "configuration.LookupEntry", on_delete=models.PROTECT, verbose_name="catégorie"
    )
    additional_categories = models.ManyToManyField(
        "configuration.LookupEntry",
        blank=True,
        related_name="additional_cobweb_items",
        verbose_name="catégories complémentaires",
    )
    kind = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="typed_cobweb_items",
        verbose_name="type d’entrée",
    )
    title = models.CharField("titre", max_length=200)
    subtitle = models.CharField("sous-titre", max_length=200, blank=True)
    reference = models.CharField("référence", max_length=64, blank=True)
    content = models.TextField("contenu", max_length=20000, blank=True)
    link = models.URLField(
        "lien", max_length=500, blank=True, validators=[URLValidator(schemes=["https", "http"])]
    )
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("actif dans le back-office", default=True)
    seo_title = models.CharField("titre SEO", max_length=200, blank=True)
    seo_description = models.CharField("description SEO", max_length=300, blank=True)
    seo_keywords = models.CharField("mots-clés SEO", max_length=300, blank=True)
    created_at = models.DateTimeField("création", auto_now_add=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)

    class Meta:
        ordering = ["position", "title", "pk"]
        permissions = [("publish_cobwebitem", "Peut publier les entrées Cobweb")]
        indexes = [models.Index(fields=["site", "is_active", "position"])]
        verbose_name = "entrée Cobweb"
        verbose_name_plural = "entrées Cobweb"

    def clean(self):
        super().clean()
        if self.category_id and self.site_id:
            lookup = self.category.lookup_list
            if (
                lookup.site_id != self.site_id
                or lookup.module != "cobweb"
                or lookup.kind != "category"
                or lookup.field_key not in ("", "cobweb_item.category")
            ):
                raise ValidationError({"category": "Choisissez une catégorie Cobweb de ce site."})
        if self.kind_id:
            lookup = self.kind.lookup_list
            if (
                lookup.site_id != self.site_id
                or lookup.module != "cobweb"
                or lookup.kind != "choice"
                or lookup.field_key != "cobweb_item.kind"
            ):
                raise ValidationError(
                    {"kind": "Choisissez un type d’entrée configuré pour ce site."}
                )

    def __str__(self):
        return self.title


class CobwebGroup(PublicationFields):
    """A site-local ordered composition of Cobweb entries."""

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT, related_name="cobweb_groups")
    category = models.ForeignKey(
        "configuration.LookupEntry", on_delete=models.PROTECT, verbose_name="catégorie"
    )
    additional_categories = models.ManyToManyField(
        "configuration.LookupEntry",
        blank=True,
        related_name="additional_cobweb_groups",
        verbose_name="catégories complémentaires",
    )
    presentation = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="styled_cobweb_groups",
        verbose_name="présentation des éléments",
    )
    title = models.CharField("titre", max_length=200)
    content = models.TextField("description", max_length=20000, blank=True)
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("actif", default=True)
    seo_title = models.CharField("titre SEO", max_length=200, blank=True)
    seo_description = models.CharField("description SEO", max_length=300, blank=True)
    seo_keywords = models.CharField("mots-clés SEO", max_length=300, blank=True)
    created_at = models.DateTimeField("création", auto_now_add=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)

    class Meta:
        ordering = ["position", "title", "pk"]
        permissions = [("publish_cobwebgroup", "Peut publier les groupes Cobweb")]
        indexes = [models.Index(fields=["site", "is_active", "position"])]
        verbose_name = "groupe Cobweb"
        verbose_name_plural = "groupes Cobweb"

    def clean(self):
        super().clean()
        if self.category_id and self.site_id:
            lookup = self.category.lookup_list
            if (
                lookup.site_id != self.site_id
                or lookup.module != "cobweb"
                or lookup.kind != "category"
                or lookup.field_key not in ("", "cobweb_group.category")
            ):
                raise ValidationError(
                    {"category": "Choisissez une catégorie de groupe Cobweb du site."}
                )
        if self.presentation_id:
            lookup = self.presentation.lookup_list
            if (
                lookup.site_id != self.site_id
                or lookup.module != "cobweb"
                or lookup.kind != "choice"
                or lookup.field_key != "cobweb_group.presentation"
            ):
                raise ValidationError(
                    {"presentation": "Choisissez une présentation configurée pour ce site."}
                )

    def __str__(self):
        return self.title


class CobwebGroupMember(models.Model):
    group = models.ForeignKey(CobwebGroup, on_delete=models.CASCADE, related_name="members")
    item = models.ForeignKey(CobwebItem, on_delete=models.PROTECT, related_name="group_memberships")
    position = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["group", "item"], name="unique_cobweb_group_item")
        ]
        indexes = [models.Index(fields=["group", "position"])]
        verbose_name = "membre de groupe Cobweb"
        verbose_name_plural = "membres de groupe Cobweb"

    def clean(self):
        super().clean()
        if self.group_id and self.item_id and self.group.site_id != self.item.site_id:
            raise ValidationError(
                {"item": "Une entrée d’un autre site ne peut pas être liée au groupe."}
            )
