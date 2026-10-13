from django import forms
from django.db.models import Q

from apps.configuration.models import LookupEntry
from apps.media.models import Asset

from .content import clean_html
from .models import CobwebGroup, CobwebItem

PUBLICATION_FIELDS = ("is_online", "is_searchable", "starts_at", "ends_at")


def configure_content_form(form, site, field_key, can_publish, can_view_media, *, module="cobweb"):
    allowed = Q(is_active=True, lookup_list__is_active=True)
    if form.instance.pk:
        allowed |= Q(pk__in=form.instance.additional_categories.values_list("pk", flat=True))
    form.fields["additional_categories"].queryset = site_categories(
        site, field_key, module=module
    ).filter(allowed)
    for name in ("picture", "thumbnail"):
        form.fields[name].queryset = Asset.objects.filter(site=site)
        if not can_view_media:
            form.fields[name].queryset = form.fields[name].queryset.filter(
                pk=getattr(form.instance, f"{name}_id")
            )
            form.fields[name].disabled = True
    for name in PUBLICATION_FIELDS:
        form.fields[name].disabled = not can_publish
    for name in ("starts_at", "ends_at"):
        form.fields[name].widget = forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        )
    form.fields["content"].widget.attrs["class"] = "rich-text-source"
    form.fields[
        "content"
    ].help_text = "Texte mis en forme. Les contenus non autorisés sont retirés à l’enregistrement."


def configured_choices(site, field_key, current_id=None, *, module="cobweb"):
    allowed = Q(is_active=True, lookup_list__is_active=True)
    if current_id:
        allowed |= Q(pk=current_id)
    return (
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module=module,
            lookup_list__kind="choice",
            lookup_list__field_key=field_key,
        )
        .filter(allowed)
        .select_related("lookup_list")
    )


def site_categories(site, field_key, *, module="cobweb"):
    return LookupEntry.objects.filter(
        lookup_list__site=site,
        lookup_list__module=module,
        lookup_list__kind="category",
        lookup_list__field_key__in=("", field_key) if module == "cobweb" else (field_key,),
    ).select_related("lookup_list")


class CategoryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        suffix = " (inactive)" if not obj.is_active or not obj.lookup_list.is_active else ""
        return f"{obj.lookup_list.name} / {obj.label}{suffix}"


class CobwebItemForm(forms.ModelForm):
    category = CategoryChoiceField(label="Catégorie", queryset=LookupEntry.objects.none())
    version = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = CobwebItem
        fields = [
            "title",
            "subtitle",
            "reference",
            "category",
            "content",
            "link",
            "position",
            "is_active",
            "additional_categories",
            "kind",
            "picture",
            "thumbnail",
            "seo_title",
            "seo_description",
            "seo_keywords",
            *PUBLICATION_FIELDS,
        ]
        widgets = {"content": forms.Textarea(attrs={"rows": 10})}
        help_texts = {
            "content": "Texte simple. Le contenu HTML est affiché comme du texte.",
            "link": "Lien HTTP ou HTTPS. Aucune adresse n’est chargée par le serveur.",
            "is_active": (
                "L’activation concerne le back-office ; "
                "elle ne publie pas l’entrée sur le site web."
            ),
        }

    def __init__(self, *args, site, can_publish=False, can_view_media=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        allowed = Q(is_active=True, lookup_list__is_active=True)
        if self.instance.pk:
            allowed |= Q(pk=self.instance.category_id)
            self.fields["version"].required = True
            self.initial["version"] = self.instance.updated_at.isoformat()
        self.fields["category"].queryset = site_categories(site, "cobweb_item.category").filter(
            allowed
        )
        configure_content_form(self, site, "cobweb_item.category", can_publish, can_view_media)
        self.fields["kind"].queryset = configured_choices(
            site, "cobweb_item.kind", self.instance.kind_id
        )

    def clean_content(self):
        return clean_html(self.cleaned_data["content"])


class CobwebSearchForm(forms.Form):
    q = forms.CharField(
        label="Recherche",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"type": "search", "placeholder": "Titre, référence ou contenu"}
        ),
    )
    category = CategoryChoiceField(
        label="Catégorie",
        queryset=LookupEntry.objects.none(),
        required=False,
        empty_label="Toutes les catégories",
    )
    active = forms.ChoiceField(
        label="État",
        required=False,
        choices=[("", "Tous les états"), ("yes", "Actif"), ("no", "Inactif")],
    )
    online = forms.ChoiceField(
        label="Visibilité en ligne",
        required=False,
        choices=[
            ("", "Toutes les visibilités"),
            ("yes", "Visible en ligne"),
            ("no", "Hors ligne"),
        ],
    )
    include_descendants = forms.BooleanField(label="Inclure les sous-catégories", required=False)
    created_from = forms.DateField(
        label="Créée à partir du",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    created_until = forms.DateField(
        label="Créée jusqu’au",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    order = forms.ChoiceField(
        label="Trier par",
        required=False,
        choices=[
            ("position", "Ordre d’affichage"),
            ("title", "Titre A → Z"),
            ("-title", "Titre Z → A"),
            ("-created_at", "Création récente"),
            ("-updated_at", "Modification récente"),
        ],
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = site_categories(site, "cobweb_item.category")

    def clean(self):
        data = super().clean()
        start, end = data.get("created_from"), data.get("created_until")
        if start and end and start > end:
            self.add_error("created_until", "La date de fin doit suivre la date de début.")
        return data


class DeleteConfirmationForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="Je confirme la suppression définitive de cette entrée.")


class CobwebGroupForm(forms.ModelForm):
    category = CategoryChoiceField(label="Catégorie", queryset=LookupEntry.objects.none())
    version = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = CobwebGroup
        fields = [
            "title",
            "category",
            "content",
            "position",
            "is_active",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "additional_categories",
            "presentation",
            "picture",
            "thumbnail",
            *PUBLICATION_FIELDS,
        ]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 8}),
            "seo_description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "presentation": (
                "Cartes, liste ou diaporama. Un diaporama contient uniquement des entrées "
                "Cobweb qui ont une image."
            ),
        }

    def __init__(self, *args, site, can_publish=False, can_view_media=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        allowed = Q(is_active=True, lookup_list__is_active=True)
        if self.instance.pk:
            allowed |= Q(pk=self.instance.category_id)
            self.fields["version"].required = True
            self.initial["version"] = self.instance.updated_at.isoformat()
        self.fields["category"].queryset = site_categories(site, "cobweb_group.category").filter(
            allowed
        )
        configure_content_form(self, site, "cobweb_group.category", can_publish, can_view_media)
        self.fields["presentation"].queryset = configured_choices(
            site, "cobweb_group.presentation", self.instance.presentation_id
        )
        if not self.instance.pk and self.initial.get("presentation"):
            self.fields["presentation"].initial = self.initial["presentation"]

    def clean_content(self):
        return clean_html(self.cleaned_data["content"])


class CobwebGroupSearchForm(CobwebSearchForm):
    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, site=site, **kwargs)
        self.fields["category"].queryset = site_categories(site, "cobweb_group.category")


class GroupMembersForm(forms.Form):
    """Validates only IDs that are available in the current site's item queryset."""

    members = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, site, existing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = (
            CobwebItem.objects.filter(site=site)
            .select_related("category__lookup_list", "picture", "thumbnail")
            .order_by("category__label", "position", "title", "pk")
        )
        self.fields["members"].choices = [(str(item.pk), item.title) for item in self.items]
        self.existing = existing or {}
        if not self.is_bound:
            self.initial["members"] = [str(pk) for pk in self.existing]

    def clean(self):
        data = super().clean()
        selected = {int(value) for value in data.get("members", [])}
        permitted = {item.pk for item in self.items}
        if not selected <= permitted:
            raise forms.ValidationError("Une entrée sélectionnée ne fait pas partie de ce site.")
        positions = {}
        for pk in selected:
            raw = self.data.get(f"member_position_{pk}", "").strip()
            try:
                position = int(raw)
            except ValueError:
                self.add_error(
                    "members", "Chaque entrée sélectionnée doit avoir un ordre entier positif."
                )
                continue
            if not 0 <= position <= 2_147_483_647:
                self.add_error("members", "L’ordre doit être compris entre 0 et 2 147 483 647.")
                continue
            positions[pk] = position
        data["member_positions"] = positions
        data["member_ids"] = selected
        return data
