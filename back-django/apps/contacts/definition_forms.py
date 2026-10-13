from django import forms

from .models import QUESTION_KEYS, ContactFormDefinition


class DefinitionForm(forms.ModelForm):
    version = forms.CharField(widget=forms.HiddenInput)

    class Meta:
        model = ContactFormDefinition
        fields = ["code", "label", "is_active", "position"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].initial = (
            self.instance.updated_at.isoformat() if self.instance.pk else "new"
        )
        self.fields["code"].disabled = bool(self.instance.pk)
        self.question_rows = []
        for index, key in enumerate(QUESTION_KEYS, 1):
            setting = self.instance.question_settings.get(key, {})
            for suffix, field in (
                (
                    "active",
                    forms.BooleanField(
                        label=f"Activer la question {index}",
                        required=False,
                        initial=setting.get("active", False),
                    ),
                ),
                (
                    "label",
                    forms.CharField(
                        label="Libellé",
                        required=False,
                        max_length=200,
                        initial=setting.get("label", ""),
                    ),
                ),
                (
                    "required",
                    forms.BooleanField(
                        label="Réponse obligatoire",
                        required=False,
                        initial=setting.get("required", False),
                    ),
                ),
            ):
                self.fields[f"{key}_{suffix}"] = field
            self.question_rows.append(
                {
                    "number": index,
                    "key": key,
                    "fields": [
                        self[f"{key}_{suffix}"] for suffix in ("active", "label", "required")
                    ],
                }
            )

    def clean(self):
        data = super().clean()
        expected = self.instance.updated_at.isoformat() if self.instance.pk else "new"
        if data.get("version") != expected:
            self.add_error(None, "Ce formulaire a été modifié. Rechargez la page.")
        settings, questions = {}, []
        for key in QUESTION_KEYS:
            active, label, required = (
                data.get(f"{key}_{suffix}") for suffix in ("active", "label", "required")
            )
            label = label or ""
            settings[key] = {"active": bool(active), "label": label, "required": bool(required)}
            if any(ord(char) < 32 or ord(char) == 127 for char in label):
                self.add_error(f"{key}_label", "Les caractères de contrôle sont interdits.")
            if active:
                if not label:
                    self.add_error(f"{key}_label", "Indiquez le libellé de la question activée.")
                else:
                    questions.append({"key": key, "label": label, "required": bool(required)})
        self.instance.questions = questions
        self.instance.question_settings = settings
        return data
