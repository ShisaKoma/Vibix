from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from .constants import OWNER_SITE_ID


def site_icon_path(instance, filename):
    return f"sites/{instance.pk}/icons/{uuid4().hex}.png"


hex_color = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Indiquez une couleur au format #RRGGBB.")


class SiteQuerySet(models.QuerySet):
    def for_user(self, user):
        if not user.is_authenticated or not user.is_active:
            return self.none()
        active = self.filter(is_active=True)
        if user.is_superuser:
            return active
        return (
            active.exclude(pk=OWNER_SITE_ID)
            .filter(memberships__user=user, memberships__is_active=True)
            .distinct()
        )


class Site(models.Model):
    code = models.SlugField("code", unique=True, max_length=64)
    name = models.CharField("nom", max_length=200)
    description = models.CharField("courte description", max_length=300, blank=True)
    icon = models.FileField("icône", upload_to=site_icon_path, blank=True)
    primary_color_1 = models.CharField(
        "couleur principale 1", max_length=7, default="#175EC5", validators=[hex_color]
    )
    primary_color_2 = models.CharField(
        "couleur principale 2", max_length=7, default="#172B43", validators=[hex_color]
    )
    secondary_color_1 = models.CharField(
        "couleur secondaire 1", max_length=7, default="#E6EDFC", validators=[hex_color]
    )
    secondary_color_2 = models.CharField(
        "couleur secondaire 2", max_length=7, default="#F3F5F8", validators=[hex_color]
    )
    is_active = models.BooleanField("actif", default=True)
    articles_enabled = models.BooleanField("module Articles actif", default=True)
    pages_enabled = models.BooleanField("module Pages actif", default=True)
    cobweb_enabled = models.BooleanField("module Cobweb actif", default=True)
    planning_enabled = models.BooleanField("module Planning actif", default=True)
    products_enabled = models.BooleanField("module Produits actif", default=False)
    contacts_enabled = models.BooleanField("module Contacts actif", default=False)
    addressbook_enabled = models.BooleanField("module Carnet d’adresses actif", default=True)
    public_access_enabled = models.BooleanField("exposition publique active", default=True)
    mcp_access_enabled = models.BooleanField("accès MCP autorisé à terme", default=False)
    created_at = models.DateTimeField("création", auto_now_add=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)
    objects = SiteQuerySet.as_manager()

    class Meta:
        ordering = ["name", "pk"]
        permissions = [("manage_site_settings", "Peut configurer les paramètres du site")]
        verbose_name = "site"

    def __str__(self):
        return self.name

    def module_enabled(self, module):
        field = {
            "articles": "articles_enabled",
            "pages": "pages_enabled",
            "cobweb": "cobweb_enabled",
            "addressbook": "addressbook_enabled",
            "contacts": "contacts_enabled",
            "planning": "planning_enabled",
            "products": "products_enabled",
        }.get(module)
        return bool(field and getattr(self, field))


class Role(models.Model):
    """Reusable role definitions, granted only through a site membership."""

    code = models.SlugField("code", unique=True, max_length=64)
    name = models.CharField("nom", max_length=100)
    permissions = models.ManyToManyField("auth.Permission", blank=True, verbose_name="permissions")

    class Meta:
        ordering = ["name"]
        verbose_name = "rôle"

    def __str__(self):
        return self.name


class SiteMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="site_memberships",
        verbose_name="utilisateur",
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name="memberships", verbose_name="site"
    )
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, related_name="memberships", verbose_name="rôle"
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("création", auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "site", "role"], name="unique_user_site_role")
        ]
        verbose_name = "affectation de rôle par site"
        verbose_name_plural = "affectations de rôles par site"

    def clean(self):
        super().clean()
        if self.site_id == OWNER_SITE_ID and self.user_id and not self.user.is_superuser:
            raise ValidationError(
                {"site": "Le site 1 est réservé à l’administrateur propriétaire (owner)."}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} / {self.site} / {self.role}"
