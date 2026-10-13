from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from apps.products.models import Tariff, Variant

from .booking_models import Booking, BookingCalendar, BookingOffer, BookingWindow
from .models import Resource, validate_timezone
from .timeutils import local_datetime


class VersionedForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["version"].required = bool(self.instance.pk)
        self.initial["version"] = self.instance.version

    def _update_errors(self, errors):
        if hasattr(errors, "error_dict"):
            mapped = {}
            for key, values in errors.error_dict.items():
                mapped.setdefault(key if key in self.fields else NON_FIELD_ERRORS, []).extend(
                    values
                )
            errors = ValidationError(mapped)
        super()._update_errors(errors)


class CalendarForm(VersionedForm):
    class Meta:
        model = BookingCalendar
        fields = ["name", "timezone", "is_active"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        self.fields["timezone"].validators.append(validate_timezone)


class OfferForm(VersionedForm):
    class Meta:
        model = BookingOffer
        fields = [
            "title",
            "calendar",
            "resource",
            "variant",
            "tariff",
            "duration_minutes",
            "step_minutes",
            "before_minutes",
            "after_minutes",
            "lead_minutes",
            "horizon_days",
            "cancellation_minutes",
            "cancellation_terms",
            "recipient",
            "is_active",
        ]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.site = site
        self.fields["calendar"].queryset = BookingCalendar.objects.filter(site=site)
        self.fields["resource"].queryset = Resource.objects.filter(site=site)
        self.fields["variant"].queryset = Variant.objects.filter(product__site=site).select_related(
            "product"
        )
        self.fields["tariff"].queryset = Tariff.objects.filter(
            variant__product__site=site
        ).select_related("variant__product")
        self.fields["variant"].label_from_instance = lambda value: (
            f"{value.product.title} — {value.reference}"
        )
        self.fields["tariff"].label_from_instance = lambda value: (
            f"{value.variant.product.title} — {value.variant.reference} — {value.label}"
        )
        self.fields[
            "recipient"
        ].help_text = "Notifications à ce destinataire interne seulement, via le SMTP du site."
        self.fields["is_active"].help_text = (
            "Confirmation manuelle, capacité exclusive 1 ; l'attente n'occupe pas. "
            "Produit et déclinaison doivent être publiés et réservables."
        )


class WindowForm(VersionedForm):
    starts_at = forms.CharField(
        label="Début local", widget=forms.TextInput(attrs={"type": "datetime-local"})
    )
    ends_at = forms.CharField(
        label="Fin locale", widget=forms.TextInput(attrs={"type": "datetime-local"})
    )

    class Meta:
        model = BookingWindow
        fields = ["calendar", "resource", "starts_at", "ends_at", "is_closed", "is_active"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["calendar"].queryset = BookingCalendar.objects.filter(site=site)
        self.fields["resource"].queryset = Resource.objects.filter(site=site)
        if self.instance.pk:
            for key in ("starts_at", "ends_at"):
                self.initial[key] = (
                    getattr(self.instance, key)
                    .astimezone(ZoneInfo(self.instance.calendar.timezone))
                    .strftime("%Y-%m-%dT%H:%M")
                )
        for key in ("starts_at", "ends_at"):
            self.fields[
                key
            ].help_text = "Heure locale dans le fuseau du planning sélectionné ; fin exclusive."

    def clean(self):
        data = super().clean()
        calendar = data.get("calendar")
        if calendar:
            for key in ("starts_at", "ends_at"):
                try:
                    if data.get(key):
                        data[key] = local_datetime(data[key], calendar.timezone)
                except ValidationError as error:
                    self.add_error(key, error)
        return data


class AvailabilityForm(forms.Form):
    offer = forms.ModelChoiceField(label="Offre", queryset=BookingOffer.objects.none())
    date = forms.DateField(label="Jour", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["offer"].queryset = BookingOffer.objects.filter(site=site).select_related(
            "calendar"
        )

    def clean_date(self):
        value = self.cleaned_data["date"]
        if not 1900 <= value.year <= 2100:
            raise ValidationError("Choisissez une date entre 1900 et 2100.")
        return value


class CreateForm(forms.Form):
    starts_at = forms.CharField(
        label="Début local", widget=forms.TextInput(attrs={"type": "datetime-local"})
    )
    name = forms.CharField(label="Nom du demandeur", max_length=200)
    email = forms.EmailField(label="Courriel du demandeur (sans envoi automatique)", required=False)
    phone = forms.CharField(label="Téléphone", max_length=32, required=False)
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    quote_token = forms.CharField(widget=forms.HiddenInput, max_length=2000)
    confirm = forms.BooleanField(
        label="Je confirme le récapitulatif et crée une demande en attente non occupante"
    )

    def __init__(self, *args, zone, **kwargs):
        super().__init__(*args, **kwargs)
        self.zone = zone
        self.fields[
            "starts_at"
        ].help_text = f"Heure dans {zone}. Durée et marges fixées par l'offre."

    def clean_starts_at(self):
        return local_datetime(self.cleaned_data["starts_at"], self.zone)

    def clean(self):
        data = super().clean()
        for key in ("name", "email", "phone"):
            if any(ord(char) < 32 or ord(char) == 127 for char in data.get(key, "")):
                self.add_error(key, "Les caractères de contrôle sont interdits.")
        return data


class ActionForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(label="Motif", max_length=1000)
    starts_at = forms.CharField(
        label="Nouveau début local",
        required=False,
        widget=forms.TextInput(attrs={"type": "datetime-local"}),
    )
    confirm = forms.BooleanField(label="Je confirme cette action et ses conséquences")

    def __init__(self, *args, zone, moving=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.zone = zone
        if moving:
            self.fields["starts_at"].required = True
            self.fields[
                "starts_at"
            ].help_text = (
                f"Heure locale dans {zone}. L'ancien créneau est conservé si le déplacement échoue."
            )
        else:
            del self.fields["starts_at"]

    def clean_starts_at(self):
        return local_datetime(self.cleaned_data["starts_at"], self.zone)


class FilterForm(forms.Form):
    state = forms.ChoiceField(
        label="État", required=False, choices=[("", "Tous"), *Booking.State.choices]
    )
    resource = forms.ModelChoiceField(
        label="Ressource", required=False, queryset=Resource.objects.none()
    )
    calendar = forms.ModelChoiceField(
        label="Planning", required=False, queryset=BookingCalendar.objects.none()
    )
    date_from = forms.DateField(
        label="Depuis", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label="Jusqu'au", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resource"].queryset = Resource.objects.filter(site=site)
        self.fields["calendar"].queryset = BookingCalendar.objects.filter(site=site)

    def clean(self):
        data = super().clean()
        left, right = data.get("date_from"), data.get("date_to")
        if left and right and right < left:
            self.add_error("date_to", "La fin de période doit suivre son début.")
        return data
