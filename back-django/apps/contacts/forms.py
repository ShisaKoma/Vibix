from django import forms
from django.db.models import Q

from apps.addressbook.models import ContactPoint, Entry, EntryType
from apps.cobweb.forms import CategoryChoiceField, site_categories
from apps.configuration.models import LookupEntry

from .models import ContactFormDefinition, ContactRequest


class SubmissionForm(forms.Form):
    name = forms.CharField(label="Nom", max_length=200)
    first_name = forms.CharField(label="Prénom", max_length=100, required=False)
    email = forms.EmailField(label="Courriel", max_length=254)
    phone = forms.CharField(label="Téléphone", max_length=32, required=False)
    organization = forms.CharField(label="Organisation", max_length=200, required=False)
    title = forms.CharField(label="Objet", max_length=200)
    message = forms.CharField(
        label="Message", max_length=50000, widget=forms.Textarea(attrs={"rows": 8})
    )
    form_type = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)
    form_version = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)
    challenge = forms.CharField(max_length=2000, required=False, widget=forms.HiddenInput)
    website = forms.CharField(max_length=200, required=False, widget=forms.HiddenInput)

    def __init__(self, *args, max_length=10000, schema=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.schema = schema or {
            "code": "web-contact",
            "label": "Contact",
            "version": "legacy",
            "questions": [],
        }
        self.fields["form_type"].initial = self.schema["code"]
        self.fields["form_version"].initial = self.schema["version"]
        for question in self.schema["questions"]:
            self.fields[question["key"]] = forms.CharField(
                label=question["label"],
                required=question["required"],
                max_length=2000,
                widget=forms.Textarea(attrs={"rows": 2}),
            )
        self.fields["message"].max_length = max_length
        self.fields["message"].widget.attrs["maxlength"] = max_length

    def clean(self):
        data = super().clean()
        if data.get("form_type") and data["form_type"] != self.schema["code"]:
            self.add_error("form_type", "Ce formulaire ne correspond pas à la demande.")
        if (data.get("form_version") and data["form_version"] != self.schema["version"]) or (
            self.schema["questions"] and not data.get("form_version")
        ):
            self.add_error(
                "form_version", "Le formulaire a changé. Rechargez-le avant de recommencer."
            )
        data["form_type"] = self.schema["code"]
        data["form_version"] = self.schema["version"]
        for question in self.schema["questions"]:
            if "\x00" in data.get(question["key"], ""):
                self.add_error(question["key"], "La réponse contient un caractère interdit.")
        for field in ("name", "first_name", "email", "phone", "organization", "title"):
            if any(ord(char) < 32 or ord(char) == 127 for char in data.get(field, "")):
                self.add_error(field, "Les caractères de contrôle sont interdits.")
        if data.get("phone"):
            try:
                ContactPoint(mode="phone", value=data["phone"]).clean()
            except forms.ValidationError:
                self.add_error("phone", "Indiquez un téléphone de 7 à 15 chiffres.")
        if len(data.get("message", "")) > self.fields["message"].max_length:
            self.add_error("message", "Le message dépasse la longueur autorisée.")
        if "\x00" in data.get("message", ""):
            self.add_error("message", "Le message contient un caractère interdit.")
        if data.get("email"):
            local, domain = data["email"].rsplit("@", 1)
            data["email"] = f"{local}@{domain.lower()}"
        return data


class TreatmentForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    status = forms.ChoiceField(label="Statut", choices=ContactRequest.Status.choices)
    category = CategoryChoiceField(
        label="Catégorie", queryset=LookupEntry.objects.none(), required=False
    )
    note = forms.CharField(
        label="Ajouter une note interne",
        max_length=5000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, contact, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = site_categories(
            contact.site, "contacts_contactrequest.category", module="contacts"
        ).filter(Q(is_active=True, lookup_list__is_active=True) | Q(pk=contact.category_id))


class VersionForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="Je confirme cette action")


class ForwardForm(VersionForm):
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    recipient = forms.EmailField(label="Destinataire", max_length=254)
    introduction = forms.CharField(
        label="Message d’accompagnement",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirm = forms.BooleanField(
        label="Je confirme la retransmission du message et des coordonnées à ce destinataire"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["version", "submission_id", "recipient", "introduction", "confirm"])

    def clean_recipient(self):
        value = self.cleaned_data["recipient"]
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise forms.ValidationError("Indiquez une seule adresse email valide.")
        return value


class ContactSearchForm(forms.Form):
    q = forms.CharField(label="Nom, courriel ou objet", max_length=200, required=False)
    status = forms.ChoiceField(
        label="Statut",
        required=False,
        choices=[("", "Tous les statuts"), *ContactRequest.Status.choices],
    )
    form_type = forms.ChoiceField(label="Type de formulaire", required=False)
    category = forms.ChoiceField(label="Catégorie", required=False)
    state = forms.ChoiceField(
        label="Emplacement", choices=[("active", "Demandes actives"), ("trash", "Corbeille")]
    )
    order = forms.ChoiceField(
        label="Trier par",
        choices=[
            ("-created_at", "Plus récentes"),
            ("created_at", "Plus anciennes"),
            ("title", "Objet A–Z"),
            ("-title", "Objet Z–A"),
            ("name", "Nom A–Z"),
            ("-name", "Nom Z–A"),
            ("status", "Statut"),
        ],
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        definitions = {
            item.code: item.label for item in ContactFormDefinition.objects.filter(site=site)
        }
        codes = set(definitions) | set(
            ContactRequest.objects.filter(site=site).values_list("form_type", flat=True)
        )
        self.fields["form_type"].choices = [
            ("", "Tous les formulaires"),
            ("__untyped__", "Historique sans type"),
            *[(code, definitions.get(code, code)) for code in sorted(codes) if code],
        ]
        self.fields["category"].choices = [
            ("", "Toutes les catégories"),
            ("none", "Sans catégorie"),
            *[
                (
                    str(entry.pk),
                    entry.label
                    + (
                        " (inactive)"
                        if not entry.is_active or not entry.lookup_list.is_active
                        else ""
                    ),
                )
                for entry in site_categories(
                    site, "contacts_contactrequest.category", module="contacts"
                )
            ],
        ]


class LinkForm(VersionForm):
    mode = forms.ChoiceField(
        label="Action",
        choices=[
            ("create", "Créer une personne"),
            ("link", "Associer une fiche existante"),
            ("sync", "Associer et compléter / mettre à jour"),
        ],
    )
    entry = forms.ModelChoiceField(
        label="Personne existante", queryset=Entry.objects.none(), required=False
    )
    entry_type = forms.ModelChoiceField(
        label="Type de la nouvelle personne", queryset=EntryType.objects.none(), required=False
    )
    entry_version = forms.CharField(widget=forms.HiddenInput, required=False)
    update_name = forms.BooleanField(
        label="Remplacer le nom et le prénom par ceux de la demande", required=False
    )
    add_email = forms.BooleanField(
        label="Ajouter le courriel s’il est absent", required=False, initial=True
    )
    add_phone = forms.BooleanField(
        label="Ajouter le téléphone s’il est absent", required=False, initial=True
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["entry"].queryset = Entry.objects.filter(
            site=site, kind="person", is_active=True, archived_at__isnull=True
        )
        self.fields["entry_type"].queryset = EntryType.objects.filter(
            site=site, kind="person", is_active=True
        )

    def clean(self):
        data = super().clean()
        if data.get("mode") == "create" and not data.get("entry_type"):
            self.add_error("entry_type", "Choisissez un type de personne.")
        if data.get("mode") in {"link", "sync"} and not data.get("entry"):
            self.add_error("entry", "Choisissez une personne de ce site.")
        return data


class ClientForm(forms.Form):
    name = forms.CharField(label="Nom du client", max_length=100)
    expires_in_days = forms.IntegerField(
        label="Validité (jours)", min_value=1, max_value=365, initial=90
    )
