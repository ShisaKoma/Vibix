import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from apps.cobweb.content import clean_html

from .publication import validate_timezone, validate_variant_features, validate_window


def validate_reference(value):
    if not value.strip() or any(ord(character) < 32 for character in value):
        raise ValidationError("Indiquez une référence sans caractère de contrôle.")


def same_site(instance, fields):
    errors = {}
    if instance.site_id == 1:
        errors["site"] = "Le site réservé ne reçoit pas de produits client."
    for field in fields:
        target = getattr(instance, field, None)
        if target and target.site_id != instance.site_id:
            errors[field] = "Choisissez une référence du même site."
    return errors


def validate_lookup(entry, site_id, field_key):
    if entry and (
        entry.lookup_list.site_id != site_id
        or entry.lookup_list.module != "products"
        or entry.lookup_list.field_key != field_key
    ):
        raise ValidationError("Choisissez une valeur du référentiel Produits de ce site.")


class Brand(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    code = models.SlugField("code", max_length=64)
    name = models.CharField("nom", max_length=120)
    description = models.TextField("présentation", blank=True, max_length=5000)
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("active", default=True)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["position", "name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("code"), "site", name="products_brand_code_unique")
        ]
        verbose_name = "marque"

    def clean(self):
        self.code = self.code.lower()
        if self.pk and self.product_set.exists():
            previous = Brand.objects.get(pk=self.pk)
            if previous.code != self.code:
                raise ValidationError({"code": "Le code d’une marque utilisée doit rester stable."})
        if errors := same_site(self, []):
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class Product(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    reference = models.CharField("référence", max_length=100, validators=[validate_reference])
    title = models.CharField("titre", max_length=200)
    subtitle = models.CharField("sous-titre", blank=True, max_length=300)
    category = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products_primary",
        verbose_name="catégorie principale",
    )
    additional_categories = models.ManyToManyField(
        "configuration.LookupEntry",
        blank=True,
        related_name="products_secondary",
        verbose_name="catégories secondaires",
    )
    language = models.ForeignKey(
        "configuration.LookupEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products_languages",
        verbose_name="langue",
    )
    brand = models.ForeignKey(
        Brand, null=True, blank=True, on_delete=models.PROTECT, verbose_name="marque"
    )
    summary = models.TextField("description courte", blank=True, max_length=2000)
    content = models.TextField("contenu", blank=True, max_length=50000)
    terms = models.TextField("conditions", blank=True, max_length=10000)
    picture = models.ForeignKey(
        "media.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products_pictures",
        verbose_name="image principale",
    )
    thumbnail = models.ForeignKey(
        "media.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products_thumbnails",
        verbose_name="vignette",
    )
    slug = models.SlugField("adresse SEO", blank=True, max_length=200)
    url_language = models.CharField(max_length=64, default="default", editable=False)
    seo_title = models.CharField("titre SEO", blank=True, max_length=200)
    seo_description = models.CharField("description SEO", blank=True, max_length=500)
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("actif", default=True)
    is_online = models.BooleanField("publier sur le site", default=False)
    starts_at = models.DateTimeField("début de publication", null=True, blank=True)
    ends_at = models.DateTimeField("fin de publication", null=True, blank=True)
    timezone = models.CharField(
        "fuseau de publication",
        max_length=64,
        default="Europe/Paris",
        validators=[validate_timezone],
    )
    featured = models.BooleanField("favori éditorial", default=False)
    is_new = models.BooleanField("nouveauté", default=False)
    in_home = models.BooleanField("à l’accueil", default=False)
    is_reservable = models.BooleanField("offre réservable à configurer", default=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "title", "pk"]
        verbose_name = "produit"
        permissions = [
            ("publish_product", "Peut publier et dépublier les produits et déclinaisons"),
            ("restore_product", "Peut restaurer les produits"),
            ("view_commercial_data", "Peut consulter le stock descriptif privé"),
            ("change_commercial_data", "Peut modifier le stock descriptif privé"),
            ("configure_reservations", "Peut préparer le caractère réservable"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(starts_at__isnull=True)
                | models.Q(ends_at__isnull=True)
                | models.Q(ends_at__gt=models.F("starts_at")),
                name="products_publication_window",
            ),
            models.UniqueConstraint(Lower("reference"), "site", name="products_reference_unique"),
            models.UniqueConstraint(
                fields=["site", "url_language", "slug"],
                condition=~models.Q(slug=""),
                name="products_slug_unique",
            ),
        ]
        indexes = [models.Index(fields=["site", "deleted_at", "position"])]

    def clean(self):
        super().clean()
        self.reference = self.reference.strip()
        self.slug = self.slug.lower()
        self.url_language = self.language.value if self.language_id else "default"
        self.content = clean_html(self.content)
        validate_timezone(self.timezone)
        validate_window(self)
        errors = same_site(self, ["brand", "picture", "thumbnail"])
        for field in ("category", "language"):
            try:
                validate_lookup(getattr(self, field), self.site_id, f"products_product.{field}")
            except ValidationError as error:
                errors[field] = error.messages
        if self.is_online:
            from apps.cobweb.publication import category_available

            if not self.category_id or not category_available(self.category):
                errors["category"] = (
                    "Une catégorie principale et ses ancêtres actifs sont requis pour publier."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title

    @property
    def publication_label(self):
        from .publication import publication_label

        return publication_label(self)


class Variant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    reference = models.CharField("référence", max_length=100, validators=[validate_reference])
    title = models.CharField("titre", max_length=200)
    content = models.TextField(
        "contenu propre",
        blank=True,
        max_length=50000,
        help_text="Vide : reprend le contenu du produit.",
    )
    picture = models.ForeignKey(
        "media.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name="image propre",
        related_name="products_variants",
    )
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("active", default=True)
    is_online = models.BooleanField("publier cette déclinaison", default=False)
    starts_at = models.DateTimeField("début de publication", null=True, blank=True)
    ends_at = models.DateTimeField("fin de publication", null=True, blank=True)
    length = models.DecimalField(
        "longueur",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    width = models.DecimalField(
        "largeur",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    height = models.DecimalField(
        "hauteur",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    dimension_unit = models.CharField(
        "unité des dimensions",
        max_length=16,
        blank=True,
        choices=[("mm", "mm"), ("cm", "cm"), ("m", "m")],
    )
    weight = models.DecimalField(
        "poids",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    weight_unit = models.CharField(
        "unité du poids", max_length=16, blank=True, choices=[("g", "g"), ("kg", "kg")]
    )
    stock_quantity = models.DecimalField(
        "stock descriptif privé",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    sold_quantity = models.DecimalField(
        "quantité vendue historique",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["position", "title", "pk"]
        verbose_name = "déclinaison"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(starts_at__isnull=True)
                | models.Q(ends_at__isnull=True)
                | models.Q(ends_at__gt=models.F("starts_at")),
                name="products_variant_window",
            ),
            models.UniqueConstraint(
                Lower("reference"), "product", name="products_variant_reference_unique"
            ),
            *[
                models.CheckConstraint(
                    condition=models.Q(**{f"{field}__gte": 0})
                    | models.Q(**{f"{field}__isnull": True}),
                    name=f"products_variant_{field}_positive",
                )
                for field in (
                    "length",
                    "width",
                    "height",
                    "weight",
                    "stock_quantity",
                    "sold_quantity",
                )
            ],
        ]

    def clean(self):
        super().clean()
        self.reference = self.reference.strip()
        self.content = clean_html(self.content)
        validate_window(self)
        if self.is_online and self.product_id:
            validate_variant_features(self)
        errors = {}
        if self.product_id:
            if self.product.site_id == 1:
                errors["product"] = "Le site réservé ne reçoit pas de déclinaisons."
            if self.picture_id and self.picture.site_id != self.product.site_id:
                errors["picture"] = "Choisissez une image du même site."
            if self.pk:
                previous = (
                    Variant.objects.filter(pk=self.pk).values_list("product_id", flat=True).first()
                )
                if previous != self.product_id:
                    errors["product"] = "Le produit d’une déclinaison existante est immuable."
        if any(getattr(self, key) is not None for key in ("length", "width", "height")):
            if not self.dimension_unit:
                errors["dimension_unit"] = "Précisez l’unité des dimensions."
        if self.weight is not None and not self.weight_unit:
            errors["weight_unit"] = "Précisez l’unité du poids."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title

    @property
    def publication_label(self):
        from .publication import publication_label

        return publication_label(self)


class FeatureDefinition(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Texte"
        NUMBER = "number", "Nombre décimal"
        DATE = "date", "Date"
        BOOLEAN = "boolean", "Oui / non"
        CHOICE = "choice", "Choix"

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    code = models.SlugField("code", max_length=64)
    name = models.CharField("libellé", max_length=120)
    kind = models.CharField("type", max_length=16, choices=Kind.choices, default=Kind.TEXT)
    unit = models.CharField("unité", max_length=30, blank=True)
    choices = models.TextField(
        "choix autorisés",
        max_length=5000,
        blank=True,
        help_text="Un choix par ligne ; pour le type Choix uniquement.",
    )
    required = models.BooleanField("requis avant publication", default=False)
    is_public = models.BooleanField("visible lors de la future publication", default=True)
    is_active = models.BooleanField("active", default=True)
    position = models.PositiveIntegerField("ordre", default=0)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["position", "name", "pk"]
        verbose_name = "définition de caractéristique"
        constraints = [
            models.UniqueConstraint(Lower("code"), "site", name="products_feature_code_unique")
        ]

    @property
    def options(self):
        return [value.strip() for value in self.choices.splitlines() if value.strip()]

    def clean(self):
        self.code = self.code.lower()
        errors = same_site(self, [])
        if not self.pk and FeatureDefinition.objects.filter(site_id=self.site_id).count() >= 200:
            errors["code"] = "Le site est limité à 200 définitions, désactivées comprises."
        if self.kind == self.Kind.CHOICE:
            if not self.options or len(set(self.options)) != len(self.options):
                errors["choices"] = "Indiquez au moins un choix, sans doublon."
            elif any(len(value) > 2000 for value in self.options):
                errors["choices"] = "Chaque choix est limité à 2 000 caractères."
        elif self.choices:
            errors["choices"] = "Les choix concernent uniquement le type Choix."
        if self.pk and self.values.exists():
            previous = FeatureDefinition.objects.get(pk=self.pk)
            if previous.code != self.code:
                errors["code"] = "Le code d’une caractéristique utilisée doit rester stable."
            if (previous.kind, previous.unit, previous.choices) != (
                self.kind,
                self.unit,
                self.choices,
            ):
                errors["kind"] = (
                    "Créez une nouvelle définition pour changer un format déjà utilisé."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


def normalized_feature_value(definition, value):
    value = str(value).strip()
    if len(value) > 2000:
        raise ValidationError("La valeur dépasse 2 000 caractères.")
    if not value:
        return ""
    if definition.kind == FeatureDefinition.Kind.NUMBER:
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            raise ValidationError("Indiquez un décimal avec un point, sans séparateur de milliers.")
        try:
            number = Decimal(value)
            if not number.is_finite() or len(number.as_tuple().digits) > 28:
                raise InvalidOperation
            return format(number, "f")
        except InvalidOperation as error:
            raise ValidationError("Indiquez un décimal fini de 28 chiffres maximum.") from error
    if definition.kind == FeatureDefinition.Kind.DATE:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            return date.fromisoformat(value).isoformat()
        except ValueError as error:
            raise ValidationError("Indiquez une date AAAA-MM-JJ valide.") from error
    if definition.kind == FeatureDefinition.Kind.BOOLEAN and value not in ("true", "false"):
        raise ValidationError("Choisissez Oui ou Non.")
    if definition.kind == FeatureDefinition.Kind.CHOICE and value not in definition.options:
        raise ValidationError("Choisissez une valeur autorisée par cette définition.")
    return value


class FeatureValue(models.Model):
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="features")
    definition = models.ForeignKey(
        FeatureDefinition, on_delete=models.PROTECT, related_name="values"
    )
    value = models.CharField("valeur", max_length=2000)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "definition"], name="products_feature_value_unique"
            )
        ]

    def clean(self):
        if self.variant_id and self.definition_id:
            if self.variant.product.site_id != self.definition.site_id:
                raise ValidationError("La caractéristique doit appartenir au site du produit.")
            self.value = normalized_feature_value(self.definition, self.value)


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="gallery")
    variant = models.ForeignKey(
        Variant,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="gallery",
        verbose_name="déclinaison facultative",
    )
    asset = models.ForeignKey(
        "media.Asset",
        on_delete=models.PROTECT,
        related_name="products_gallery",
        verbose_name="image",
    )
    alt_text = models.CharField("texte alternatif", max_length=300)
    position = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        ordering = ["position", "pk"]
        verbose_name = "image de produit"

    def clean(self):
        errors = {}
        if self.product_id and self.product.site_id == 1:
            errors["product"] = "Le site réservé ne reçoit pas de galerie client."
        if self.product_id and self.asset_id and self.product.site_id != self.asset.site_id:
            errors["asset"] = "Choisissez une image du même site."
        if self.variant_id and self.variant.product_id != self.product_id:
            errors["variant"] = "Choisissez une déclinaison de ce produit."
        if errors:
            raise ValidationError(errors)


class Tariff(models.Model):
    """Prix d'un conditionnement, rattaché à une déclinaison immuable."""

    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="tariffs")
    label = models.CharField("libellé", max_length=120)
    unit = models.CharField("unité / conditionnement", max_length=64)
    quantity = models.DecimalField(
        "quantité du conditionnement",
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    currency = models.CharField("devise (code de trois lettres)", max_length=3)
    basis = models.CharField("base de saisie", max_length=3, choices=[("HT", "HT"), ("TTC", "TTC")])
    amount = models.DecimalField(
        "prix du conditionnement",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    on_request = models.BooleanField("prix sur demande", default=False)
    tax_rate = models.DecimalField(
        "taux de taxe en %",
        max_digits=7,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    purchase_amount = models.DecimalField(
        "coût d'achat privé",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    purchase_basis = models.CharField(
        "base du coût d'achat", max_length=3, blank=True, choices=[("HT", "HT"), ("TTC", "TTC")]
    )
    discount_kind = models.CharField(
        "type de réduction",
        max_length=8,
        blank=True,
        choices=[("amount", "Montant"), ("percent", "Pourcentage")],
    )
    discount_value = models.DecimalField(
        "réduction",
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    discount_enabled = models.BooleanField("activer la promotion", default=False)
    discount_starts_at = models.DateTimeField("début de promotion", null=True, blank=True)
    discount_ends_at = models.DateTimeField("fin de promotion", null=True, blank=True)
    terms = models.TextField("conditions tarifaires", blank=True, max_length=5000)
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("actif", default=True)
    is_public = models.BooleanField("exposer le tarif", default=False)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "pk"]
        verbose_name = "tarif"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="products_tariff_quantity"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    discount_kind="",
                    discount_value__isnull=True,
                    discount_enabled=False,
                    discount_starts_at__isnull=True,
                    discount_ends_at__isnull=True,
                )
                | models.Q(
                    discount_kind__in=["amount", "percent"],
                    discount_value__gte=0,
                    discount_value__isnull=False,
                    discount_starts_at__isnull=False,
                    discount_ends_at__isnull=False,
                    discount_ends_at__gt=models.F("discount_starts_at"),
                    on_request=False,
                ),
                name="products_tariff_discount",
            ),
            models.CheckConstraint(
                condition=~models.Q(discount_kind="percent") | models.Q(discount_value__lte=100),
                name="products_tariff_percent",
            ),
            models.CheckConstraint(
                condition=~models.Q(discount_kind="amount")
                | models.Q(discount_value__lte=models.F("amount")),
                name="products_tariff_reduction",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__isnull=True) | models.Q(amount__gte=0),
                name="products_tariff_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(purchase_amount__isnull=True) | models.Q(purchase_amount__gte=0),
                name="products_tariff_purchase",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__isnull=True)
                | models.Q(tax_rate__gte=0, tax_rate__lte=100),
                name="products_tariff_tax",
            ),
            models.CheckConstraint(
                condition=models.Q(basis__in=["HT", "TTC"]), name="products_tariff_basis"
            ),
            models.CheckConstraint(
                condition=models.Q(on_request=True, amount__isnull=True)
                | models.Q(on_request=False, amount__isnull=False),
                name="products_tariff_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(purchase_amount__isnull=True, purchase_basis="")
                | models.Q(purchase_amount__isnull=False, purchase_basis__in=["HT", "TTC"]),
                name="products_tariff_purchase_basis",
            ),
        ]

    def clean(self):
        super().clean()
        from .pricing import validate_currency

        if not self.pk and Tariff.objects.filter(variant_id=self.variant_id).count() >= 100:
            raise ValidationError("La déclinaison est limitée à 100 tarifs, archivés compris.")
        self.currency = self.currency.upper()
        validate_currency(self.currency)
        errors = {}
        for field in ("quantity", "amount", "tax_rate", "purchase_amount", "discount_value"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                errors[field] = "Indiquez un décimal fini valide."
        if errors:
            raise ValidationError(errors)
        if self.variant_id:
            if self.variant.product.site_id == 1:
                errors["variant"] = "Le site réservé ne reçoit pas de tarifs."
            if (
                self.pk
                and Tariff.objects.filter(pk=self.pk).exclude(variant_id=self.variant_id).exists()
            ):
                errors["variant"] = "La déclinaison d'un tarif existant est immuable."
        if self.on_request == (self.amount is not None):
            errors["amount"] = (
                "Choisissez un montant explicite ou prix sur demande, jamais les deux."
            )
        if (self.purchase_amount is None) != (not self.purchase_basis):
            errors["purchase_basis"] = (
                "Précisez la base uniquement lorsqu'un coût d'achat est saisi."
            )
        if self.discount_kind == "amount" and self.discount_value is not None:
            from .pricing import money

            self.discount_value = money(self.discount_value)
        if not self.discount_kind:
            if (
                self.discount_value is not None
                or self.discount_enabled
                or self.discount_starts_at
                or self.discount_ends_at
            ):
                errors["discount_kind"] = "Choisissez un type de promotion ou videz ses réglages."
        else:
            if self.on_request or self.amount is None:
                errors["discount_kind"] = "Une promotion nécessite un prix explicite."
            if self.discount_value is None:
                errors["discount_value"] = "Renseignez la réduction."
            elif self.discount_kind == "percent" and self.discount_value > 100:
                errors["discount_value"] = "Le pourcentage ne peut pas dépasser 100."
            elif (
                self.discount_kind == "amount"
                and self.amount is not None
                and self.discount_value > self.amount
            ):
                errors["discount_value"] = "La réduction ne peut pas dépasser le prix."
            if (
                not self.discount_starts_at
                or not self.discount_ends_at
                or self.discount_ends_at <= self.discount_starts_at
            ):
                errors["discount_ends_at"] = (
                    "Une promotion exige un début et une fin strictement postérieure."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.label


from .stock_models import (  # noqa: E402,F401
    StockBalance,
    StockInventory,
    StockInventoryLine,
    StockItem,
    StockLocation,
    StockMovement,
    StockOperation,
)
