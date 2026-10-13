import re
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError

from apps.planning.timeutils import local_datetime

from .forms import VersionedForm
from .models import Tariff
from .pricing import money


class StrictDecimalField(forms.DecimalField):
    def to_python(self, value):
        if value not in self.empty_values and not re.fullmatch(r"\d+(?:\.\d+)?", str(value)):
            raise ValidationError(
                "Indiquez un décimal non négatif avec un point, sans exposant ni séparateur."
            )
        return super().to_python(value)


class MoneyField(forms.CharField):
    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        if not re.fullmatch(r"\d{1,12}(?:\.\d{1,8})?", value):
            raise ValidationError(
                "Indiquez un montant positif avec un point décimal, sans séparateur."
            )
        try:
            result = money(Decimal(value))
            if result >= Decimal("1000000000000"):
                raise InvalidOperation
            return result
        except InvalidOperation as error:
            raise ValidationError("Le montant dépasse la limite autorisée.") from error


class TariffForm(VersionedForm):
    parent_version = forms.IntegerField(widget=forms.HiddenInput)
    variant_version = forms.IntegerField(widget=forms.HiddenInput)
    amount = MoneyField(
        label="Prix du conditionnement",
        required=False,
        max_length=21,
        help_text="Arrondi à deux décimales, demi supérieur. Un point comme séparateur.",
    )
    purchase_amount = MoneyField(label="Coût d'achat privé", required=False, max_length=21)

    quantity = StrictDecimalField(
        label="Quantité du conditionnement",
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )
    tax_rate = StrictDecimalField(
        label="Taux de taxe en %",
        max_digits=7,
        decimal_places=4,
        min_value=0,
        max_value=100,
        required=False,
    )
    discount_value = StrictDecimalField(
        label="Réduction", max_digits=14, decimal_places=4, min_value=0, required=False
    )
    discount_starts_at = forms.CharField(
        label="Début de promotion",
        required=False,
        widget=forms.TextInput(attrs={"type": "datetime-local"}),
    )
    discount_ends_at = forms.CharField(
        label="Fin de promotion",
        required=False,
        widget=forms.TextInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = Tariff
        fields = [
            "label",
            "unit",
            "quantity",
            "currency",
            "basis",
            "amount",
            "on_request",
            "tax_rate",
            "discount_kind",
            "discount_value",
            "discount_enabled",
            "discount_starts_at",
            "discount_ends_at",
            "purchase_amount",
            "purchase_basis",
            "terms",
            "position",
            "is_active",
            "is_public",
        ]

    def __init__(self, *args, variant, can_publish, can_change_commercial, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.variant = variant
        self.initial.update(parent_version=variant.product.version, variant_version=variant.version)
        self.zone = variant.product.timezone
        for key in ("discount_starts_at", "discount_ends_at"):
            value = getattr(self.instance, key)
            self.fields[key].help_text = f"Heure locale dans {self.zone}. Fin exclusive."
            if value:
                self.initial[key] = value.astimezone(ZoneInfo(self.zone)).strftime("%Y-%m-%dT%H:%M")
        self.can_publish = can_publish
        self.can_change_commercial = can_change_commercial
        self.fields["tax_rate"].help_text = (
            "Taux explicite, de 0 à 100 %. Vide : le montant dérivé reste inconnu. "
            "Aucun taux légal prérempli."
        )
        if not can_change_commercial:
            del self.fields["purchase_amount"]
            del self.fields["purchase_basis"]
        if not can_publish:
            del self.fields["is_public"]
            if self.instance.is_public:
                self.fields["is_active"].disabled = True

    def clean(self):
        data = super().clean()
        for key in ("discount_starts_at", "discount_ends_at"):
            try:
                data[key] = local_datetime(data[key], self.zone) if data.get(key) else None
            except ValidationError as error:
                self.add_error(key, error)
        forbidden = set()
        if not self.can_change_commercial:
            forbidden.update({"purchase_amount", "purchase_basis"} & set(self.data))
        if not self.can_publish:
            forbidden.update({"is_public"} & set(self.data))
            if self.fields["is_active"].disabled:
                forbidden.update({"is_active"} & set(self.data))
        if forbidden:
            self.add_error(None, "Vous ne disposez pas du droit pour modifier ces champs protégés.")
        return data


class TariffPublicationForm(VersionedForm):
    parent_version = forms.IntegerField(widget=forms.HiddenInput)
    variant_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Tariff
        fields = ["is_active", "is_public"]

    def __init__(self, *args, variant, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.variant = variant
        self.initial.update(parent_version=variant.product.version, variant_version=variant.version)
