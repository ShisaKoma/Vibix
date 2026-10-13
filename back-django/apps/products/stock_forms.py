from decimal import Decimal

from django import forms

from .forms import VersionedForm
from .models import Variant
from .stock_models import StockBalance, StockItem, StockLocation, StockOperation
from .tariff_forms import StrictDecimalField


def quantity_field(label, *, required=True, positive=False):
    return StrictDecimalField(
        label=label,
        required=required,
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001") if positive else Decimal("0"),
    )


def locations(site):
    return StockLocation.objects.filter(site=site).select_related("parent__parent")


def items(site):
    return StockItem.objects.filter(site=site).select_related("variant__product")


class LocationForm(VersionedForm):
    class Meta:
        model = StockLocation
        fields = ["code", "name", "kind", "parent", "address", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        self.fields["parent"].queryset = (
            locations(site).filter(kind__in=["depot", "zone"]).exclude(pk=self.instance.pk)
        )
        if self.instance.pk:
            for field in ("kind", "parent"):
                self.fields[field].disabled = True


class ItemForm(VersionedForm):
    class Meta:
        model = StockItem
        fields = ["variant", "unit"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        self.fields["variant"].queryset = Variant.objects.filter(
            product__site=site,
            product__deleted_at__isnull=True,
            archived_at__isnull=True,
            stock_item__isnull=True,
        ).select_related("product")
        self.fields["variant"].label_from_instance = lambda v: (
            f"{v.product.reference} / {v.reference} — {v.title}"
        )


class BalanceForm(VersionedForm):
    minimum = quantity_field("Seuil minimum (vide : aucun seuil)", required=False)

    class Meta:
        model = StockBalance
        fields = ["location", "minimum"]

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.item = item
        self.fields["location"].queryset = locations(item.site).filter(kind="bin")
        if self.instance.pk:
            self.fields["location"].disabled = True
        self.fields[
            "minimum"
        ].help_text = (
            f"Alerte lorsque la quantité est inférieure ou égale au seuil, en {item.unit}."
        )

    def clean(self):
        data = super().clean()
        location = data.get("location")
        if not self.instance.pk and location and not location.available:
            self.add_error("location", "Réactivez d'abord l'emplacement et ses parents.")
        return data


class StockFilterForm(forms.Form):
    q = forms.CharField(label="Référence ou titre", required=False, max_length=200)
    location = forms.ModelChoiceField(
        label="Emplacement", queryset=StockLocation.objects.none(), required=False
    )
    low = forms.BooleanField(label="Sous le seuil uniquement", required=False)

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = locations(site).filter(kind="bin")


class SelectionForm(forms.Form):
    item = forms.ModelChoiceField(label="Déclinaison / unité", queryset=StockItem.objects.none())
    source = forms.ModelChoiceField(
        label="Emplacement de départ", queryset=StockLocation.objects.none(), required=False
    )
    destination = forms.ModelChoiceField(
        label="Emplacement d'arrivée", queryset=StockLocation.objects.none(), required=False
    )

    def __init__(self, *args, site, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.fields["item"].queryset = items(site)
        for field in ("source", "destination"):
            self.fields[field].queryset = locations(site).filter(kind="bin")
            if field == "source" and kind == "entry" or field == "destination" and kind == "exit":
                del self.fields[field]
            else:
                self.fields[field].required = True

    def clean(self):
        data = super().clean()
        if data.get("source") and data.get("source") == data.get("destination"):
            self.add_error("destination", "Choisissez un emplacement différent du départ.")
        return data


class MovementForm(forms.Form):
    source_version = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)
    destination_version = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    quantity = quantity_field("Quantité", positive=True)
    reason = forms.CharField(
        label="Motif", max_length=1000, widget=forms.Textarea(attrs={"rows": 3})
    )
    confirm = forms.BooleanField(label="Je confirme ce mouvement et les emplacements indiqués.")


class InventoryCreateForm(forms.Form):
    location = forms.ModelChoiceField(
        label="Emplacement à inventorier", queryset=StockLocation.objects.none()
    )
    reason = forms.CharField(
        label="Motif / périmètre", max_length=1000, widget=forms.Textarea(attrs={"rows": 3})
    )
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(
        label="Ouvrir un inventaire de tous les stocks configurés dans cet emplacement."
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = locations(site).filter(kind="bin")


class CountForm(forms.Form):
    line_id = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    counted = quantity_field("Quantité comptée (vide : pas encore comptée)", required=False)


class OperationFilterForm(forms.Form):
    item = forms.ModelChoiceField(
        label="Déclinaison", required=False, queryset=StockItem.objects.none()
    )
    location = forms.ModelChoiceField(
        label="Emplacement", required=False, queryset=StockLocation.objects.none()
    )
    kind = forms.ChoiceField(
        label="Type",
        required=False,
        choices=[("", "Tous"), *StockOperation._meta.get_field("kind").choices],
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = items(site)
        self.fields["location"].queryset = locations(site).filter(kind="bin")


class InventoryActionForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    confirm = forms.BooleanField(label="Je confirme les conséquences indiquées.")
