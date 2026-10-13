from django import forms
from django.db.models import Q

from apps.configuration.models import LookupEntry

from .models import Address, ContactPoint, Employment, Entry, EntryType, ExtraInfo


def reference_choices(site, field, current=None):
    return LookupEntry.objects.filter(
        lookup_list__site=site,
        lookup_list__module="addressbook",
        lookup_list__field_key=f"addressbook_entry.{field}",
    ).filter(Q(is_active=True, lookup_list__is_active=True) | Q(pk=current))


def child_reference_choices(entry, key, current=None):
    return (
        LookupEntry.objects.filter(
            lookup_list__site=entry.site,
            lookup_list__module="addressbook",
            lookup_list__kind="choice",
            lookup_list__field_key=key,
        )
        .filter(Q(addressbook_kind__in=["", entry.kind]) | Q(pk=current))
        .filter(Q(is_active=True, lookup_list__is_active=True) | Q(pk=current))
    )


class KindScopedSelect(forms.Select):
    """Expose each option's family for progressive filtering in the editor."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, "instance"):
            item = value.instance
            option["attrs"]["data-addressbook-kind"] = getattr(
                item, "addressbook_kind", getattr(item, "kind", "")
            )
        return option


class EntryForm(forms.ModelForm):
    version = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Entry
        fields = [
            "kind",
            "entry_type",
            "name",
            "first_name",
            "gender",
            "salutation",
            "reference",
            "category",
            "legal_form",
            "origin",
            "notes",
            "is_active",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 5}),
            **{
                field: KindScopedSelect()
                for field in (
                    "entry_type",
                    "category",
                    "gender",
                    "salutation",
                    "legal_form",
                    "origin",
                )
            },
        }

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        self.fields["entry_type"].queryset = EntryType.objects.filter(site=site).filter(
            Q(is_active=True) | Q(pk=self.instance.entry_type_id)
        )
        self.fields["entry_type"].required = True
        self.fields["entry_type"].help_text = "Type configurable du site, lié à la famille choisie."
        for field in ("category", "gender", "salutation", "legal_form", "origin"):
            self.fields[field].queryset = reference_choices(
                site, field, getattr(self.instance, f"{field}_id")
            )
        if self.instance.pk:
            self.fields["version"].required = True
            self.initial["version"] = self.instance.updated_at.isoformat()


class SearchForm(forms.Form):
    q = forms.CharField(label="Nom, référence, coordonnée ou ville", max_length=200, required=False)
    kind = forms.ChoiceField(
        label="Type", choices=[("", "Tous les types"), *Entry.Kind.choices], required=False
    )
    category = forms.ModelChoiceField(
        label="Catégorie", queryset=LookupEntry.objects.none(), required=False
    )
    state = forms.ChoiceField(
        label="État",
        choices=[
            ("active", "Actives"),
            ("inactive", "Inactives"),
            ("archived", "Archivées"),
            ("all", "Toutes"),
        ],
        required=False,
    )
    order = forms.ChoiceField(
        label="Tri",
        choices=[("name", "Nom"), ("-updated_at", "Dernière modification")],
        required=False,
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="addressbook",
            lookup_list__field_key="addressbook_entry.category",
        )


class VersionForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)


class ChildForm(forms.ModelForm):
    version = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, entry, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry = entry
        self.initial["version"] = entry.updated_at.isoformat()


class PrimaryForm(ChildForm):
    # Non-model form field: the service switches the principal atomically before
    # saving. Existing principal rows must not cause ModelForm uniqueness errors.
    is_primary = forms.BooleanField(label="Principale", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["is_primary"] = self.instance.is_primary

    def clean(self):
        data = super().clean()
        self.instance.is_primary = data.get("is_primary", False)
        return data


class ContactForm(PrimaryForm):
    class Meta:
        model = ContactPoint
        fields = ["mode", "label", "value", "is_active"]


class AddressForm(PrimaryForm):
    class Meta:
        model = Address
        fields = [
            "address_type",
            "label",
            "street",
            "postal_code",
            "city",
            "country",
            "is_active",
        ]

    def __init__(self, *args, entry, **kwargs):
        super().__init__(*args, entry=entry, **kwargs)
        self.fields["address_type"].queryset = child_reference_choices(
            entry, "addressbook_address.address_type", self.instance.address_type_id
        )
        self.fields["address_type"].required = True


class EmploymentForm(ChildForm):
    class Meta:
        model = Employment
        fields = ["organization", "address", "function", "function_type", "starts_on", "ends_on"]
        widgets = {
            field: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            for field in ("starts_on", "ends_on")
        }

    def __init__(self, *args, entry, **kwargs):
        super().__init__(*args, entry=entry, **kwargs)
        self.fields["function_type"].queryset = LookupEntry.objects.filter(
            lookup_list__site=entry.site,
            lookup_list__module="addressbook",
            lookup_list__field_key="addressbook_employment.function_type",
        ).filter(
            Q(is_active=True, lookup_list__is_active=True) | Q(pk=self.instance.function_type_id)
        )
        self.fields["organization"].queryset = (
            Entry.objects.filter(site=entry.site)
            .exclude(kind=Entry.Kind.PERSON)
            .filter(
                Q(is_active=True, archived_at__isnull=True) | Q(pk=self.instance.organization_id)
            )
        )
        # The submitted organization narrows the address choices too; clean()
        # independently checks the relationship and the site's boundary.
        selected = (
            self.data.get("organization") if self.is_bound else self.initial.get("organization")
        )
        try:
            organization_id = int(selected or 0)
        except (ValueError, TypeError):
            organization_id = 0
        self.fields["address"].queryset = Address.objects.filter(
            entry__site=entry.site,
            entry_id=organization_id,
        ).filter(Q(is_active=True) | Q(pk=self.instance.address_id))
        self.fields[
            "address"
        ].help_text = (
            "Choisissez l’organisation, enregistrez, puis ajoutez son adresse si nécessaire."
        )


class ExtraInfoForm(ChildForm):
    class Meta:
        model = ExtraInfo
        fields = ["label", "info_type", "value", "is_active"]
        help_texts = {"value": "Date : AAAA-MM-JJ ; nombre : point décimal ; booléen : oui ou non."}

    def __init__(self, *args, entry, **kwargs):
        super().__init__(*args, entry=entry, **kwargs)
        self.fields["info_type"].queryset = child_reference_choices(
            entry, "addressbook_extrainfo.info_type", self.instance.info_type_id
        )
        self.fields["info_type"].required = True


CHILD_FORMS = {
    "contacts": (ContactPoint, ContactForm, "Coordonnée"),
    "addresses": (Address, AddressForm, "Adresse"),
    "employments": (Employment, EmploymentForm, "Rattachement professionnel"),
    "extra-info": (ExtraInfo, ExtraInfoForm, "Information complémentaire"),
}


class EntryTypeForm(forms.ModelForm):
    class Meta:
        model = EntryType
        fields = ["kind", "code", "label", "is_active"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        if self.instance.pk:
            for field in ("kind", "code"):
                self.fields[field].disabled = True

    def clean_code(self):
        code = self.cleaned_data["code"]
        if (
            EntryType.objects.filter(site=self.instance.site, code=code)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError("Ce code existe déjà pour ce site.")
        return code
