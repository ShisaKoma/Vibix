"""Stock privé localisé ; écritures métier dans stock.py, jamais par le catalogue."""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from .models import validate_reference

MAX_QUANTITY = Decimal("9999999999.9999")
MAX_INVENTORY_LINES = 500


class StockLocation(models.Model):
    class Kind(models.TextChoices):
        DEPOT = "depot", "Dépôt / magasin"
        ZONE = "zone", "Zone"
        BIN = "bin", "Emplacement"

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    code = models.CharField("code", max_length=64, validators=[validate_reference])
    name = models.CharField("nom", max_length=160)
    kind = models.CharField("type", max_length=8, choices=Kind.choices)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="dépôt ou zone parent",
    )
    address = models.TextField("adresse / indications", max_length=1000, blank=True)
    is_active = models.BooleanField("actif", default=True)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["code", "pk"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(Lower("code"), "site", name="stock_location_code_unique"),
            models.CheckConstraint(condition=~models.Q(site_id=1), name="stock_location_site"),
            models.CheckConstraint(
                condition=models.Q(kind="depot", parent__isnull=True)
                | models.Q(kind__in=["zone", "bin"], parent__isnull=False),
                name="stock_location_parent",
            ),
        ]

    def clean(self):
        self.code = self.code.strip()
        errors = {}
        if self.site_id == 1:
            errors["site"] = "Le site réservé ne reçoit pas de stock."
        if self.kind == "depot" and self.parent_id:
            errors["parent"] = "Un dépôt n'a pas de parent."
        if self.kind in ("zone", "bin") and not self.parent_id:
            errors["parent"] = "Choisissez un dépôt ou une zone."
        if self.parent_id:
            if self.parent_id == self.pk or self.parent.site_id != self.site_id:
                errors["parent"] = "Choisissez un parent du même site, distinct de cette fiche."
            elif self.parent.kind not in (["depot"] if self.kind == "zone" else ["depot", "zone"]):
                errors["parent"] = "Une zone dépend d'un dépôt ; un emplacement d'un dépôt ou zone."
            elif self.is_active and not self.parent.available:
                errors["is_active"] = "Réactivez d'abord le dépôt ou la zone parent."
        if self.pk:
            old = StockLocation.objects.get(pk=self.pk)
            if (old.site_id, old.kind, old.parent_id) != (self.site_id, self.kind, self.parent_id):
                errors["parent"] = "Le site, type et parent sont immuables ; créez un autre lieu."
            if not self.is_active:
                ids = [self.pk, *self.children.values_list("pk", flat=True)]
                ids += list(
                    StockLocation.objects.filter(parent_id__in=ids).values_list("pk", flat=True)
                )
                if StockBalance.objects.filter(location_id__in=ids, quantity__gt=0).exists():
                    errors["is_active"] = "Transférez ou sortez les quantités avant désactivation."
                if StockInventory.objects.filter(location_id__in=ids, state="draft").exists():
                    errors["is_active"] = "Terminez ou annulez les inventaires ouverts."
        if errors:
            raise ValidationError(errors)

    @property
    def available(self):
        return self.is_active and (not self.parent_id or self.parent.available)

    def __str__(self):
        parent = f"{self.parent.code} / " if self.parent_id else ""
        if self.parent_id and self.parent.parent_id:
            parent = f"{self.parent.parent.code} / {parent}"
        return f"{parent}{self.code} — {self.name}"


class StockItem(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    variant = models.OneToOneField(
        "products.Variant", on_delete=models.PROTECT, related_name="stock_item"
    )
    unit = models.CharField(
        "unité de stock",
        max_length=32,
        help_text="Ex. pièce, kg, litre. Aucune conversion implicite.",
    )
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["variant__product__reference", "variant__reference", "pk"]
        default_permissions = ()
        permissions = [
            ("view_stock", "Peut consulter les stocks localisés et leur historique"),
            ("manage_stock", "Peut configurer les unités, emplacements de stock et seuils"),
            ("manage_stock_locations", "Peut gérer les dépôts, zones et emplacements"),
            ("record_stock_movement", "Peut enregistrer les entrées et sorties de stock"),
            ("transfer_stock", "Peut transférer le stock entre emplacements"),
            ("manage_stock_inventory", "Peut préparer, compter et annuler les inventaires"),
            ("validate_stock_inventory", "Peut valider un inventaire et ses corrections"),
        ]
        constraints = [
            models.CheckConstraint(condition=~models.Q(site_id=1), name="stock_item_site")
        ]

    def clean(self):
        self.unit = self.unit.strip()
        if not self.unit or any(ord(c) < 32 for c in self.unit):
            raise ValidationError(
                {"unit": "Renseignez une unité explicite sans caractère de contrôle."}
            )
        if self.variant_id and (self.variant.product.site_id != self.site_id or self.site_id == 1):
            raise ValidationError({"variant": "Choisissez une déclinaison du même site."})
        if self.pk:
            old = StockItem.objects.get(pk=self.pk)
            if (old.site_id, old.variant_id, old.unit) != (
                self.site_id,
                self.variant_id,
                self.unit,
            ):
                raise ValidationError(
                    "La déclinaison, le site et l'unité d'un stock sont immuables."
                )

    def __str__(self):
        return (
            f"{self.variant.product.reference} / {self.variant.reference} — "
            f"{self.variant.title} ({self.unit})"
        )


class StockBalance(models.Model):
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="balances")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="balances")
    quantity = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0"), editable=False
    )
    minimum = models.DecimalField(
        "seuil minimum",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        ordering = ["location__code", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["item", "location"], name="stock_balance_unique"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0, quantity__lte=MAX_QUANTITY),
                name="stock_balance_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum__isnull=True)
                | models.Q(minimum__gte=0, minimum__lte=MAX_QUANTITY),
                name="stock_minimum_positive",
            ),
        ]

    def clean(self):
        if self.item_id and self.location_id:
            if self.item.site_id != self.location.site_id or self.location.kind != "bin":
                raise ValidationError("Le stock doit être dans un emplacement du même site.")
        if not self.pk and self.location_id:
            if (
                StockBalance.objects.filter(location_id=self.location_id).count()
                >= MAX_INVENTORY_LINES
            ):
                raise ValidationError(
                    "Un emplacement accepte au plus 500 déclinaisons suivies. "
                    "Choisissez un autre emplacement."
                )
        if self.pk:
            old = StockBalance.objects.get(pk=self.pk)
            if (old.item_id, old.location_id) != (self.item_id, self.location_id):
                raise ValidationError("L'association déclinaison/emplacement est immuable.")
        if self.quantity is not None and not self.quantity.is_finite():
            raise ValidationError("La quantité doit être finie.")

    @property
    def low(self):
        return self.minimum is not None and self.quantity <= self.minimum


class StockInventory(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT)
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    state = models.CharField(
        max_length=12,
        default="draft",
        choices=[("draft", "À compter"), ("validated", "Validé"), ("cancelled", "Annulé")],
    )
    reason = models.TextField("motif / périmètre", max_length=1000)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_inventories_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_inventories_closed",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(site_id=1), name="stock_inventory_site"),
            models.UniqueConstraint(
                fields=["location"],
                condition=models.Q(state="draft"),
                name="stock_inventory_open_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["draft", "validated", "cancelled"]),
                name="stock_inventory_state",
            ),
            models.CheckConstraint(
                condition=models.Q(state="draft", closed_at__isnull=True, closed_by__isnull=True)
                | models.Q(
                    state__in=["validated", "cancelled"],
                    closed_at__isnull=False,
                    closed_by__isnull=False,
                ),
                name="stock_inventory_closed",
            ),
        ]

    def clean(self):
        if self.location_id and (
            self.location.site_id != self.site_id or self.location.kind != "bin"
        ):
            raise ValidationError("Choisissez un emplacement du même site.")
        if self.pk:
            old = StockInventory.objects.get(pk=self.pk)
            if (old.site_id, old.location_id) != (self.site_id, self.location_id):
                raise ValidationError("Le lieu d'un inventaire est immuable.")


class StockInventoryLine(models.Model):
    inventory = models.ForeignKey(StockInventory, on_delete=models.PROTECT, related_name="lines")
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT)
    expected = models.DecimalField(max_digits=14, decimal_places=4)
    balance_version = models.PositiveIntegerField()
    counted = models.DecimalField(
        "quantité comptée",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        default_permissions = ()
        ordering = ["balance__item__variant__reference", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["inventory", "balance"], name="stock_inventory_line_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(expected__gte=0, expected__lte=MAX_QUANTITY),
                name="stock_inventory_expected_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(counted__isnull=True)
                | models.Q(counted__gte=0, counted__lte=MAX_QUANTITY),
                name="stock_inventory_count_positive",
            ),
        ]

    def clean(self):
        if self.inventory_id and self.balance_id:
            if (
                self.balance.item.site_id != self.inventory.site_id
                or self.balance.location_id != self.inventory.location_id
            ):
                raise ValidationError("La ligne doit appartenir à l'emplacement inventorié.")
        if self.inventory_id and self.inventory.state != "draft":
            raise ValidationError("Un inventaire clos ne se modifie pas.")
        if self.pk:
            old = StockInventoryLine.objects.get(pk=self.pk)
            if (old.inventory_id, old.balance_id) != (self.inventory_id, self.balance_id):
                raise ValidationError("Le stock et l'inventaire d'une ligne sont immuables.")


class ImmutableRecord(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "L'historique de stock ne se modifie pas ; enregistrez une correction."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("L'historique de stock ne se supprime pas.")


class StockOperation(ImmutableRecord):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    submission_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    kind = models.CharField(
        max_length=12,
        choices=[
            ("entry", "Entrée"),
            ("exit", "Sortie"),
            ("transfer", "Transfert"),
            ("inventory", "Inventaire"),
        ],
    )
    reason = models.TextField("motif", max_length=1000)
    inventory = models.OneToOneField(
        StockInventory, null=True, blank=True, on_delete=models.PROTECT, related_name="operation"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "submission_id"], name="stock_operation_submission_unique"
            ),
            models.CheckConstraint(condition=~models.Q(site_id=1), name="stock_operation_site"),
            models.CheckConstraint(
                condition=models.Q(kind="inventory", inventory__isnull=False)
                | models.Q(kind__in=["entry", "exit", "transfer"], inventory__isnull=True),
                name="stock_operation_kind",
            ),
        ]

    def clean(self):
        if self.inventory_id and self.inventory.site_id != self.site_id:
            raise ValidationError("L'inventaire doit appartenir au site de l'opération.")


class StockMovement(ImmutableRecord):
    operation = models.ForeignKey(
        StockOperation, on_delete=models.PROTECT, related_name="movements"
    )
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT, related_name="movements")
    delta = models.DecimalField(max_digits=14, decimal_places=4)
    before = models.DecimalField(max_digits=14, decimal_places=4)
    after = models.DecimalField(max_digits=14, decimal_places=4)
    snapshot = models.JSONField(default=dict)

    class Meta:
        default_permissions = ()
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(delta=0), name="stock_movement_nonzero"),
            models.CheckConstraint(
                condition=models.Q(
                    before__gte=0,
                    before__lte=MAX_QUANTITY,
                    after__gte=0,
                    after__lte=MAX_QUANTITY,
                ),
                name="stock_movement_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(after=models.F("before") + models.F("delta")),
                name="stock_movement_equation",
            ),
        ]

    def clean(self):
        if (
            self.balance_id
            and self.operation_id
            and self.balance.item.site_id != self.operation.site_id
        ):
            raise ValidationError("Le mouvement doit appartenir au site de l'opération.")
