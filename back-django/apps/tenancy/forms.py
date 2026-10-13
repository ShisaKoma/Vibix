import warnings
from io import BytesIO
from pathlib import Path

from django import forms
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import Site

COLOR_FIELDS = ("primary_color_1", "primary_color_2", "secondary_color_1", "secondary_color_2")


class SiteIconInput(forms.ClearableFileInput):
    template_name = "widgets/site_icon.html"


class SiteSettingsForm(forms.ModelForm):
    icon = forms.FileField(
        label="Icône du site",
        required=False,
        help_text="PNG ou JPEG, 2 Mo maximum. L’image sera ajustée à 256 × 256 pixels maximum.",
        widget=SiteIconInput(attrs={"accept": "image/png,image/jpeg"}),
    )

    class Meta:
        model = Site
        fields = ["description", "icon", *COLOR_FIELDS]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2, "maxlength": 300}),
            **{field: forms.TextInput(attrs={"type": "color"}) for field in COLOR_FIELDS},
        }

    def clean_icon(self):
        upload = self.cleaned_data.get("icon")
        if not upload or not hasattr(upload, "content_type"):
            return upload
        if upload.size > 2 * 1024 * 1024:
            raise forms.ValidationError("L’icône dépasse la limite de 2 Mo.")
        if Path(upload.name).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise forms.ValidationError("Seuls les fichiers PNG et JPEG sont acceptés.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(upload) as image:
                    if image.format not in {"PNG", "JPEG"}:
                        raise forms.ValidationError(
                            "Le fichier doit contenir une image PNG ou JPEG."
                        )
                    if image.width * image.height > 16_000_000:
                        raise forms.ValidationError("L’image dépasse 16 millions de pixels.")
                    image.verify()
                upload.seek(0)
                with Image.open(upload) as image:
                    image = ImageOps.exif_transpose(image).convert("RGBA")
                    image.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    image.save(output, format="PNG")
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise forms.ValidationError("Le fichier n’est pas une image valide.") from error
        return ContentFile(output.getvalue(), name="icon.png")

    def clean(self):
        data = super().clean()
        for field in COLOR_FIELDS:
            if data.get(field):
                data[field] = data[field].upper()
        return data


class SiteAvailabilityForm(forms.ModelForm):
    class Meta:
        model = Site
        fields = [
            "articles_enabled",
            "pages_enabled",
            "cobweb_enabled",
            "addressbook_enabled",
            "contacts_enabled",
            "planning_enabled",
            "products_enabled",
            "public_access_enabled",
            "mcp_access_enabled",
        ]
        help_texts = {
            "public_access_enabled": (
                "Contrôle l’API publique, les pages publiques et leurs médias. "
                "La gestion privée du site reste disponible."
            ),
            "mcp_access_enabled": (
                "Réglage préparatoire : aucun serveur MCP n’est encore implémenté."
            ),
        }
