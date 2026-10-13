from datetime import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db.models import Q

from apps.cobweb.forms import CategoryChoiceField
from apps.configuration.models import LookupEntry
from apps.media.models import Asset

from .models import (
    Brand,
    FeatureDefinition,
    Product,
    ProductImage,
    StockItem,
    Variant,
    normalized_feature_value,
)
from .publication import validate_timezone

PUBLICATION_FIELDS = ("is_online", "starts_at", "ends_at", "timezone")


def configure_publication(form, can_publish, zone):
    form.can_publish = can_publish
    form.publication_zone = zone
    if not can_publish:
        for key in PUBLICATION_FIELDS:
            form.fields.pop(key, None)
        if form.instance.is_online:
            form.fields["is_active"].disabled = True
        return
    for key in ("starts_at", "ends_at"):
        form.fields[key] = forms.CharField(
            label="Début de publication" if key == "starts_at" else "Fin de publication",
            required=False,
            widget=forms.TextInput(attrs={"type": "datetime-local"}),
            help_text=f"Heure locale dans le fuseau {zone}. Fin exclusive.",
        )
        value = getattr(form.instance, key)
        if value:
            form.initial[key] = value.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M")


def clean_publication(form, data):
    if not form.can_publish:
        forbidden = set(PUBLICATION_FIELDS) & set(form.data)
        if form.fields["is_active"].disabled and "is_active" in form.data:
            forbidden.add("is_active")
        if forbidden:
            form.add_error(
                None, "Le droit de publication est nécessaire pour modifier ces réglages."
            )
        return
    zone = data.get("timezone", form.publication_zone)
    try:
        validate_timezone(zone)
    except ValidationError as error:
        form.add_error("timezone" if "timezone" in form.fields else None, error)
        for key in ("starts_at", "ends_at"):
            if key in data:
                form.add_error(key, "Corrigez le fuseau avant de saisir une date.")
        return
    for key in ("starts_at", "ends_at"):
        raw = data.get(key)
        if not raw:
            data[key] = None
            continue
        try:
            local = datetime.fromisoformat(raw)
            if local.tzinfo is not None:
                raise ValueError
            first, second = (
                local.replace(tzinfo=ZoneInfo(zone), fold=0),
                local.replace(tzinfo=ZoneInfo(zone), fold=1),
            )
            if first.utcoffset() != second.utcoffset():
                raise ValueError
            data[key] = first
        except (TypeError, ValueError):
            form.add_error(key, "Choisissez une heure locale valide et non ambiguë dans ce fuseau.")


def references(site, field_key):
    return LookupEntry.objects.filter(
        lookup_list__site=site,
        lookup_list__module="products",
        lookup_list__field_key=field_key,
    ).select_related("lookup_list", "parent")


def active_or_existing(queryset, ids):
    return queryset.filter(Q(is_active=True, lookup_list__is_active=True) | Q(pk__in=ids))


class VersionedForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].required = bool(self.instance.pk)
        self.initial["version"] = self.instance.version

    def _update_errors(self, errors):
        if hasattr(errors, "error_dict"):
            mapped = {}
            for key, values in errors.error_dict.items():
                mapped.setdefault(key if key in self.fields else NON_FIELD_ERRORS, []).extend(
                    values
                )
            errors = ValidationError(mapped)
        super()._update_errors(errors)


def restrict_media(form, site, can_view_media):
    for key in ("picture", "thumbnail"):
        if key not in form.fields:
            continue
        field = form.fields[key]
        field.queryset = Asset.objects.filter(site=site)
        if not can_view_media:
            field.queryset = field.queryset.filter(pk=getattr(form.instance, f"{key}_id"))
            field.disabled = True


class ProductForm(VersionedForm):
    category = CategoryChoiceField(
        label="Catégorie principale", queryset=LookupEntry.objects.none(), required=False
    )

    class Meta:
        model = Product
        fields = [
            "reference",
            "title",
            "subtitle",
            "category",
            "additional_categories",
            "language",
            "brand",
            "position",
            "summary",
            "content",
            "terms",
            "picture",
            "thumbnail",
            "slug",
            "seo_title",
            "seo_description",
            "is_active",
            "featured",
            "is_new",
            "in_home",
            "is_reservable",
            "is_online",
            "starts_at",
            "ends_at",
            "timezone",
        ]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 10, "class": "rich-text-source"}),
            "summary": forms.Textarea(attrs={"rows": 3}),
            "terms": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(
        self,
        *args,
        site,
        can_view_media=False,
        can_configure_reservations=False,
        can_publish=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        configure_publication(self, can_publish, self.instance.timezone)
        self.fields["category"].queryset = active_or_existing(
            references(site, "products_product.category"),
            [self.instance.category_id],
        )
        existing = (
            list(self.instance.additional_categories.values_list("pk", flat=True))
            if self.instance.pk
            else []
        )
        self.fields["additional_categories"].queryset = active_or_existing(
            references(site, "products_product.category"),
            existing,
        )
        self.fields["language"].queryset = active_or_existing(
            references(site, "products_product.language"),
            [self.instance.language_id],
        )
        self.fields["brand"].queryset = Brand.objects.filter(site=site).filter(
            Q(is_active=True) | Q(pk=self.instance.brand_id)
        )
        restrict_media(self, site, can_view_media)
        if not can_configure_reservations:
            del self.fields["is_reservable"]
        else:
            self.fields["is_reservable"].label = "Autoriser les réservations internes de ce produit"
            self.fields["is_reservable"].help_text = (
                "Configurez une offre dans Planning / Réservations : ressource, plages, durée, "
                "délais et tarif éventuel. Le parcours est réservé aux gestionnaires autorisés."
            )

    def clean(self):
        data = super().clean()
        clean_publication(self, data)
        if "is_reservable" in self.data and "is_reservable" not in self.fields:
            self.add_error(None, "Le droit de configuration des réservations est nécessaire.")
        if data.get("category") in data.get("additional_categories", []):
            self.add_error(
                "additional_categories", "La catégorie principale ne doit pas être répétée."
            )
        if len(data.get("additional_categories", [])) > 200:
            self.add_error(
                "additional_categories", "Le produit est limité à 200 catégories secondaires."
            )
        for field in ("picture", "thumbnail"):
            if self.fields[field].disabled and field in self.data:
                original = str(getattr(self.instance, f"{field}_id") or "")
                if self.data.get(field) != original:
                    self.add_error(None, "Le droit de lecture de la médiathèque est nécessaire.")
        return data


class VariantForm(VersionedForm):
    parent_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Variant
        fields = [
            "reference",
            "title",
            "position",
            "is_active",
            "content",
            "picture",
            "length",
            "width",
            "height",
            "dimension_unit",
            "weight",
            "weight_unit",
            "stock_quantity",
            "sold_quantity",
            "is_online",
            "starts_at",
            "ends_at",
        ]
        widgets = {"content": forms.Textarea(attrs={"rows": 10, "class": "rich-text-source"})}

    def __init__(
        self,
        *args,
        product,
        can_view_media=False,
        can_view_commercial=False,
        can_change_commercial=False,
        can_publish=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.instance.product = product
        configure_publication(self, can_publish, product.timezone)
        self.initial["parent_version"] = product.version
        restrict_media(self, product.site, can_view_media)
        for field in ("stock_quantity", "sold_quantity"):
            if not can_view_commercial:
                del self.fields[field]
            elif not can_change_commercial:
                self.fields[field].disabled = True
        if "stock_quantity" in self.fields:
            self.fields["stock_quantity"].label = "Ancienne quantité descriptive privée"
            self.fields["stock_quantity"].help_text = (
                "Valeur historique, distincte du stock par emplacement. "
                "Aucun transfert automatique."
            )
            if self.instance.pk and StockItem.objects.filter(variant=self.instance).exists():
                self.fields["stock_quantity"].disabled = True

    def clean(self):
        data = super().clean()
        clean_publication(self, data)
        for field in ("stock_quantity", "sold_quantity", "picture"):
            if field in self.data and (field not in self.fields or self.fields[field].disabled):
                self.add_error(None, "Cette modification exige le droit dédié au champ concerné.")
        return data


class FeaturesForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    parent_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, variant, **kwargs):
        super().__init__(*args, **kwargs)
        self.variant = variant
        self.initial.update(version=variant.version, parent_version=variant.product.version)
        existing = {value.definition_id: value.value for value in variant.features.all()}
        self.definitions = list(
            FeatureDefinition.objects.filter(site=variant.product.site).filter(
                Q(is_active=True) | Q(pk__in=existing)
            )
        )
        for definition in self.definitions:
            label = definition.name + (f" ({definition.unit})" if definition.unit else "")
            help_text = (
                "Requis avant publication ; un brouillon peut rester incomplet."
                if definition.required
                else ""
            )
            if not definition.is_public:
                help_text += " Caractéristique privée."
            if not definition.is_active:
                help_text += " Définition désactivée : valeur conservée."
            field = forms.CharField(
                label=label, max_length=2000, required=False, help_text=help_text
            )
            if definition.kind in ("boolean", "choice"):
                choices = (
                    [("true", "Oui"), ("false", "Non")]
                    if definition.kind == "boolean"
                    else [(value, value) for value in definition.options]
                )
                field = forms.ChoiceField(
                    label=label,
                    required=False,
                    choices=[("", "Non renseigné"), *choices],
                    help_text=help_text,
                )
            elif definition.kind == "date":
                field.widget = forms.TextInput(attrs={"type": "date"})
            self.fields[f"feature_{definition.pk}"] = field
            self.initial[f"feature_{definition.pk}"] = existing.get(definition.pk, "")
            field.disabled = not definition.is_active

    def clean(self):
        data = super().clean()
        expected = {f"feature_{definition.pk}" for definition in self.definitions}
        if any(key.startswith("feature_") and key not in expected for key in self.data):
            self.add_error(None, "Une caractéristique n’appartient pas aux choix autorisés.")
        for definition in self.definitions:
            key = f"feature_{definition.pk}"
            if key in data:
                try:
                    data[key] = normalized_feature_value(definition, data[key])
                    if self.variant.is_online and definition.required and not data[key]:
                        self.add_error(
                            key, "Cette valeur est requise pour une déclinaison publiée."
                        )
                except ValidationError as error:
                    self.add_error(key, error)
        return data


class PublicationForm(VersionedForm):
    class Meta:
        model = Product
        fields = ["is_active", "is_online", "timezone", "starts_at", "ends_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        zone = (
            self.instance.timezone
            if isinstance(self.instance, Product)
            else self.instance.product.timezone
        )
        configure_publication(self, True, zone)

    def clean(self):
        data = super().clean()
        clean_publication(self, data)
        return data


class VariantPublicationForm(PublicationForm):
    parent_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Variant
        fields = ["is_active", "is_online", "starts_at", "ends_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["parent_version"] = self.instance.product.version


class BrandForm(VersionedForm):
    class Meta:
        model = Brand
        fields = ["code", "name", "description", "position", "is_active"]


class DefinitionForm(VersionedForm):
    class Meta:
        model = FeatureDefinition
        fields = [
            "code",
            "name",
            "kind",
            "unit",
            "choices",
            "required",
            "is_public",
            "position",
            "is_active",
        ]


class ImageForm(forms.ModelForm):
    parent_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = ProductImage
        fields = ["asset", "alt_text", "position"]

    def __init__(self, *args, product, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.product = product
        self.fields["asset"].queryset = Asset.objects.filter(site=product.site)
        self.initial["parent_version"] = product.version


class LifecycleForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    parent_version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    confirm = forms.BooleanField(label="Je confirme cette action")


class SearchForm(forms.Form):
    q = forms.CharField(label="Référence, titre ou texte", required=False, max_length=200)
    category = CategoryChoiceField(
        label="Catégorie et sous-catégories", queryset=LookupEntry.objects.none(), required=False
    )
    brand = forms.ModelChoiceField(label="Marque", queryset=Brand.objects.none(), required=False)
    language = forms.ModelChoiceField(
        label="Langue", queryset=LookupEntry.objects.none(), required=False
    )
    active = forms.ChoiceField(
        label="Activation",
        required=False,
        choices=[("", "Tous"), ("yes", "Actifs"), ("no", "Inactifs")],
    )
    featured = forms.ChoiceField(
        label="Favori éditorial",
        required=False,
        choices=[("", "Tous"), ("yes", "Oui"), ("no", "Non")],
    )
    is_new = forms.ChoiceField(
        label="Nouveauté", required=False, choices=[("", "Tous"), ("yes", "Oui"), ("no", "Non")]
    )
    in_home = forms.ChoiceField(
        label="Accueil", required=False, choices=[("", "Tous"), ("yes", "Oui"), ("no", "Non")]
    )
    reservable = forms.ChoiceField(
        label="Réservable à configurer",
        required=False,
        choices=[("", "Tous"), ("yes", "Oui"), ("no", "Non")],
    )
    order = forms.ChoiceField(
        label="Tri",
        required=False,
        choices=[
            ("position", "Ordre"),
            ("reference", "Référence"),
            ("title", "Titre A → Z"),
            ("-title", "Titre Z → A"),
            ("-created_at", "Création récente"),
            ("-updated_at", "Modification récente"),
        ],
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = references(site, "products_product.category")
        self.fields["language"].queryset = references(site, "products_product.language")
        self.fields["brand"].queryset = Brand.objects.filter(site=site)
