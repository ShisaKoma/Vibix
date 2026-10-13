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

from .models import Article, ArticleImage


class ArticleForm(forms.ModelForm):
    category = CategoryChoiceField(label="Catégorie", queryset=LookupEntry.objects.none())
    version = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Article
        fields = [
            "title",
            "subtitle",
            "reference",
            "category",
            "additional_categories",
            "language",
            "content",
            "picture",
            "thumbnail",
            "position",
            "is_active",
            "in_home",
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
            "in_home": "Présente l’article dans la liste publique d’accueil, s’il est publiable.",
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
            site, "articles_article.category", module="articles"
        ).filter(allowed)
        configure_content_form(
            self, site, "articles_article.category", can_publish, can_view_media, module="articles"
        )
        self.fields["language"].queryset = configured_choices(
            site, "articles_article.language", self.instance.language_id, module="articles"
        )
        self.fields["in_home"].disabled = not can_publish
        self.fields["picture"].label = "Image 1 — visuel principal"
        self.fields["thumbnail"].label = "Vignette pour les listes"

    def clean_content(self):
        return clean_html(self.cleaned_data["content"])


class GalleryForm(forms.Form):
    def __init__(self, *args, site, article, can_view_media=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.article = article
        existing = dict(article.gallery.values_list("position", "asset_id")) if article.pk else {}
        for position in range(2, 9):
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

    def save(self, article):
        for position in range(2, 9):
            asset = self.cleaned_data[f"gallery_{position}"]
            if asset:
                ArticleImage.objects.update_or_create(
                    article=article, position=position, defaults={"asset": asset}
                )
            else:
                article.gallery.filter(position=position).delete()


class ArticleSearchForm(CobwebSearchForm):
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
    home = forms.ChoiceField(
        label="Accueil",
        required=False,
        choices=[
            ("", "Tous les articles"),
            ("yes", "Présenté en accueil"),
            ("no", "Autres articles"),
        ],
    )
    trash = forms.ChoiceField(
        label="Emplacement",
        required=False,
        choices=[
            ("", "Articles courants"),
            ("yes", "Corbeille"),
        ],
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, site=site, **kwargs)
        self.fields["category"].queryset = site_categories(
            site, "articles_article.category", module="articles"
        )
        self.fields["language"].queryset = LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="articles",
            lookup_list__field_key="articles_article.language",
            lookup_list__kind="choice",
        )
        self.fields["author"].queryset = (
            get_user_model().objects.filter(authored_articles__site=site).distinct()
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
    confirm = forms.BooleanField(label="Je confirme cette action sur l’article.")
