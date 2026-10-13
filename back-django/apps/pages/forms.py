from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.cobweb.content import clean_html
from apps.cobweb.forms import (
    PUBLICATION_FIELDS,
    CategoryChoiceField,
    CobwebSearchForm,
    configure_content_form,
    configured_choices,
    site_categories,
)
from apps.configuration.models import LookupEntry
from apps.media.models import Asset

from .models import Page, PageImage


class PageForm(forms.ModelForm):
    category = CategoryChoiceField(label="Catégorie", queryset=LookupEntry.objects.none())
    version = forms.CharField(widget=forms.HiddenInput, required=False)

    group = CategoryChoiceField(label="Groupe", queryset=LookupEntry.objects.none(), required=False)

    class Meta:
        model = Page
        fields = [
            "title",
            "subtitle",
            "reference",
            "category",
            "additional_categories",
            "language",
            "slug",
            "group",
            "content",
            "picture",
            "thumbnail",
            "position",
            "is_active",
            "seo_title",
            "seo_description",
            "seo_keywords",
            *PUBLICATION_FIELDS,
        ]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 12}),
            "seo_description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "position": "La priorité la plus élevée apparaît en premier.",
        }

    def __init__(self, *args, site, can_publish=False, can_view_media=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        allowed = Q(is_active=True, lookup_list__is_active=True)
        if self.instance.pk:
            allowed |= Q(pk=self.instance.category_id)
            self.fields["version"].required = True
            self.initial["version"] = self.instance.updated_at.isoformat()
        self.fields["category"].queryset = site_categories(
            site, "pages_page.category", module="pages"
        ).filter(allowed)
        configure_content_form(
            self, site, "pages_page.category", can_publish, can_view_media, module="pages"
        )
        self.fields["language"].queryset = configured_choices(
            site, "pages_page.language", self.instance.language_id, module="pages"
        )
        group_allowed = Q(is_active=True, lookup_list__is_active=True)
        if self.instance.group_id:
            group_allowed |= Q(pk=self.instance.group_id)
        self.fields["group"].queryset = (
            LookupEntry.objects.filter(
                lookup_list__site=site,
                lookup_list__module="pages",
                lookup_list__kind="category",
                lookup_list__field_key="pages_page.group",
            )
            .filter(group_allowed)
            .select_related("lookup_list", "parent")
        )
        self.fields[
            "slug"
        ].help_text = "Adresse unique par site et langue ; minuscules, chiffres et tirets."
        self.fields["picture"].label = "Image 1 — visuel principal"
        self.fields["thumbnail"].label = "Vignette pour les listes"

    def clean_slug(self):
        return self.cleaned_data["slug"].lower()

    def clean(self):
        data = super().clean()
        if (
            data.get("slug")
            and Page.objects.filter(
                site=self.instance.site,
                url_language=data["language"].value if data.get("language") else "default",
                slug=data["slug"],
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            self.add_error("slug", "Cette adresse est déjà utilisée pour ce site et cette langue.")
        return data

    def clean_content(self):
        return clean_html(self.cleaned_data["content"])


class GalleryForm(forms.Form):
    def __init__(self, *args, site, page, can_view_media=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        existing = dict(page.gallery.values_list("position", "asset_id")) if page.pk else {}
        for position in range(2, 5):
            name = f"gallery_{position}"
            queryset = Asset.objects.filter(site=site)
            if not can_view_media:
                queryset = queryset.filter(pk=existing.get(position))
            self.fields[name] = forms.ModelChoiceField(
                label=f"Image {position}",
                queryset=queryset,
                required=False,
                disabled=not can_view_media,
                initial=existing.get(position),
            )

    def save(self, page):
        for position in range(2, 5):
            asset = self.cleaned_data[f"gallery_{position}"]
            if asset:
                PageImage.objects.update_or_create(
                    page=page, position=position, defaults={"asset": asset}
                )
            else:
                page.gallery.filter(position=position).delete()


class PageSearchForm(CobwebSearchForm):
    language = forms.ModelChoiceField(
        label="Langue",
        queryset=LookupEntry.objects.none(),
        required=False,
        empty_label="Toutes les langues",
    )
    author = forms.ModelChoiceField(
        label="Auteur",
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label="Tous les auteurs",
    )
    group = CategoryChoiceField(label="Groupe", queryset=LookupEntry.objects.none(), required=False)
    trash = forms.ChoiceField(
        label="Emplacement",
        required=False,
        choices=[
            ("", "Pages courantes"),
            ("yes", "Corbeille"),
        ],
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, site=site, **kwargs)
        self.fields["include_descendants"].label = "Inclure les sous-catégories et sous-groupes"
        self.fields["group"].empty_label = "Tous les groupes"
        self.fields["category"].queryset = site_categories(
            site, "pages_page.category", module="pages"
        )
        self.fields["language"].queryset = LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="pages",
            lookup_list__field_key="pages_page.language",
            lookup_list__kind="choice",
        )
        self.fields["group"].queryset = LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="pages",
            lookup_list__kind="category",
            lookup_list__field_key="pages_page.group",
        ).select_related("lookup_list", "parent")
        self.fields["author"].queryset = (
            get_user_model().objects.filter(authored_pages__site=site).distinct()
        )
        self.fields["order"].choices = [
            ("-position", "Priorité décroissante"),
            ("position", "Priorité croissante"),
            ("title", "Titre A → Z"),
            ("-title", "Titre Z → A"),
            ("-published_at", "Publication récente"),
            ("-created_at", "Création récente"),
            ("-updated_at", "Modification récente"),
        ]


class LifecycleForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="Je confirme cette action sur la page.")
