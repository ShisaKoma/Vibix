from django import forms

from apps.tenancy.models import Site


class FirstRunForm(forms.Form):
    preferred_site = forms.ModelChoiceField(
        label="Site de démarrage",
        queryset=Site.objects.none(),
        empty_label="Choisir un site",
        help_text=(
            "Ce choix règle l’ordre d’affichage de vos sites. Vos permissions restent inchangées."
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["preferred_site"].queryset = Site.objects.for_user(user)
