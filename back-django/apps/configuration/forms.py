from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import FIELD_CHOICES, LookupEntry, LookupList, SiteCommunicationSettings

CONFIGURABLE_MODULES = [
    ("products", "Produits"),
    ("api", "Fronts clients"),
    ("addressbook", "Carnet d’adresses"),
    ("cobweb", "Cobweb"),
    ("articles", "Articles"),
    ("pages", "Pages"),
]


class LookupListForm(forms.ModelForm):
    def __init__(self, *args, site=None, kind=None, **kwargs):
        if site is not None and "instance" not in kwargs:
            kwargs["instance"] = LookupList(site=site)
        super().__init__(*args, **kwargs)
        # Only fields that exist in the current product are configurable. A
        # reference must not be created for a hypothetical consumer.
        self.fields["module"].choices = CONFIGURABLE_MODULES
        kind = kind or self.instance.kind
        self.fields["field_key"].choices = [
            (key, label)
            for key, label in FIELD_CHOICES
            if not kind or (key.endswith((".category", ".group")) == (kind == "category"))
        ]
        self.original_binding = (self.instance.module, self.instance.kind, self.instance.field_key)

    def clean_code(self):
        code = self.cleaned_data["code"]
        duplicate = LookupList.objects.filter(
            site_id=self.instance.site_id,
            module=self.cleaned_data.get("module"),
            kind=self.instance.kind or self.initial.get("kind"),
            code=code,
        ).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError(
                "Une liste portant ce code existe déjà pour ce module et ce site."
            )
        return code

    class Meta:
        model = LookupList
        fields = ["module", "field_key", "code", "name", "is_active"]
        widgets = {"code": forms.TextInput(attrs={"autocomplete": "off"})}

    def clean(self):
        data = super().clean()
        if not data.get("field_key"):
            self.add_error("field_key", "Indiquez le champ métier alimenté par cette arborescence.")
        if data.get("field_key") and not data["field_key"].startswith(f"{data.get('module')}_"):
            self.add_error("field_key", "Le champ cible doit appartenir au module sélectionné.")
        if self.instance.pk:
            binding = (data.get("module"), self.instance.kind, data.get("field_key"))
            if binding != self.original_binding:
                self.add_error(
                    "field_key",
                    "Créez une nouvelle liste pour changer son champ cible ; "
                    "les liaisons existantes doivent être conservées.",
                )
        return data


class LookupEntryForm(forms.ModelForm):
    class Meta:
        model = LookupEntry
        fields = [
            "value",
            "label",
            "addressbook_kind",
            "value_kind",
            "position",
            "is_active",
            "parent",
        ]

    def __init__(self, *args, **kwargs):
        lookup_list = kwargs.pop("lookup_list", None)
        super().__init__(*args, **kwargs)
        if self.instance.lookup_list_id:
            lookup_list = self.instance.lookup_list
        if lookup_list is not None:
            self.fields["parent"].queryset = LookupEntry.objects.filter(lookup_list=lookup_list)
        else:
            self.fields["parent"].queryset = LookupEntry.objects.none()
        if lookup_list is not None and lookup_list.kind != "category":
            self.fields["parent"].disabled = True
            self.fields["parent"].widget = forms.HiddenInput()
        if lookup_list is not None and lookup_list.module != "addressbook":
            self.fields["addressbook_kind"].disabled = True
            self.fields["addressbook_kind"].widget = forms.HiddenInput()
        if lookup_list is not None and lookup_list.field_key != "addressbook_extrainfo.info_type":
            self.fields["value_kind"].disabled = True
            self.fields["value_kind"].widget = forms.HiddenInput()


class LookupTreeFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["lookup_list"] = self.instance
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        parents = dict(self.instance.entries.values_list("pk", "parent_id"))
        for form in self.forms:
            if form.cleaned_data and form.instance.pk:
                parent = form.cleaned_data.get("parent")
                parents[form.instance.pk] = parent.pk if parent else None
        for start in parents:
            cursor, seen = start, set()
            while cursor in parents and cursor is not None:
                if cursor in seen:
                    raise forms.ValidationError(
                        "Ces catégories créeraient une boucle dans l’arborescence."
                    )
                seen.add(cursor)
                cursor = parents[cursor]


LookupEntryFormSet = inlineformset_factory(
    LookupList,
    LookupEntry,
    form=LookupEntryForm,
    formset=LookupTreeFormSet,
    extra=1,
    can_delete=False,
    max_num=200,
    validate_max=True,
    absolute_max=200,
)


class CommunicationSettingsForm(forms.ModelForm):
    version = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["version"] = (
            self.instance.updated_at.isoformat() if self.instance.pk else "new"
        )

    def _update_errors(self, errors):
        # A partial form may encounter a model-level error on another section.
        if hasattr(errors, "error_dict"):
            mapped = {}
            for key, values in errors.error_dict.items():
                mapped.setdefault(key if key in self.fields else NON_FIELD_ERRORS, []).extend(
                    values
                )
            errors = ValidationError(mapped)
        super()._update_errors(errors)

    def clean(self):
        data = super().clean()
        expected = self.instance.updated_at.isoformat() if self.instance.pk else "new"
        if data.get("version") != expected:
            raise forms.ValidationError(
                "Ces réglages ont changé depuis l’ouverture de la page. "
                "Rechargez avant de réessayer."
            )
        return data


class SiteSMTPForm(CommunicationSettingsForm):
    class Meta:
        model = SiteCommunicationSettings
        fields = [
            "smtp_enabled",
            "smtp_host",
            "smtp_port",
            "smtp_security",
            "smtp_username",
            "smtp_from_email",
            "smtp_from_name",
        ]
        help_texts = {
            "smtp_host": "Nom du serveur fourni par votre prestataire de messagerie.",
            "smtp_username": "Laissez vide si le relais autorisé fonctionne sans authentification.",
        }
        widgets = {"smtp_username": forms.TextInput(attrs={"autocomplete": "off"})}

    def clean(self):
        data = super().clean()
        from .communications import smtp_password_configured

        if data.get("smtp_enabled") and data.get("smtp_username"):
            if not smtp_password_configured(self.instance.site_id):
                self.add_error("smtp_username", "Le secret SMTP doit être installé sur le serveur.")
        return data


class SiteContactPolicyForm(CommunicationSettingsForm):
    class Meta:
        model = SiteCommunicationSettings
        fields = [
            "contact_recipient",
            "contact_hourly_limit",
            "contact_ip_hourly_limit",
            "contact_max_length",
            "contact_retention_days",
            "spam_terms",
            "spam_scan_content",
            "spam_scan_metadata",
            "contact_addressbook_mode",
        ]
        widgets = {"spam_terms": forms.Textarea(attrs={"rows": 5, "maxlength": 10000})}
        help_texts = {
            "spam_terms": "Un terme ou une expression par ligne, 200 maximum. "
            "La recherche ignore casse et accents, sans expression régulière.",
            "contact_addressbook_mode": "Propose une action après validation d’une demande, "
            "toujours confirmée par un agent autorisé.",
        }

    def clean(self):
        data = super().clean()
        if (
            data.get("contact_addressbook_mode") != "manual"
            and not self.instance.site.addressbook_enabled
        ):
            self.add_error(
                "contact_addressbook_mode",
                "Activez le carnet d’adresses pour choisir cette liaison.",
            )
        return data
