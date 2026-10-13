from datetime import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import Event, EventSeries, Resource, validate_timezone
from .recurrence import TEMPLATE_FIELDS, local_instant, validate_rule

VIEWS = [
    ("day", "Jour"),
    ("week", "Semaine"),
    ("workweek", "Semaine ouvrée"),
    ("month", "Mois"),
    ("agenda", "Agenda"),
    ("timeline", "Chronologie"),
]


class EventForm(forms.ModelForm):
    starts_at = forms.CharField(
        label="Début", widget=forms.TextInput(attrs={"type": "datetime-local"})
    )
    ends_at = forms.CharField(label="Fin", widget=forms.TextInput(attrs={"type": "datetime-local"}))
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    submission_id = forms.UUIDField(widget=forms.HiddenInput)

    repeat_frequency = forms.ChoiceField(
        label="Répétition",
        required=False,
        choices=[("", "Ne pas répéter"), *EventSeries.Frequency.choices],
    )
    repeat_interval = forms.IntegerField(
        label="Tous les… (jours / semaines / mois)",
        min_value=1,
        max_value=12,
        required=False,
        initial=1,
    )
    repeat_count = forms.IntegerField(
        label="Nombre d’occurrences (première comprise)",
        min_value=2,
        max_value=100,
        required=False,
        initial=10,
        help_text=(
            "100 occurrences au maximum sur cinq ans. Les mois sans le jour choisi sont ignorés."
        ),
    )

    class Meta:
        model = Event
        fields = [
            "title",
            "kind",
            "timezone",
            "starts_at",
            "ends_at",
            "all_day",
            "description",
            "location",
            "link",
            "resources",
            "is_online",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "resources": forms.CheckboxSelectMultiple,
        }
        help_texts = {
            "timezone": "Fuseau IANA, par exemple Europe/Paris ou America/New_York.",
            "all_day": "Fin exclusive : pour le 3 octobre entier, finir le 4 octobre à 00:00.",
        }

    def __init__(self, *args, site, can_publish=False, editing_series=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.editing_series = editing_series
        self.instance.skip_booking_occupation = editing_series
        self.instance.site = site
        self.fields["resources"].queryset = (
            Resource.objects.filter(site=site)
            .filter(Q(is_active=True) | Q(event=self.instance.pk))
            .distinct()
        )
        self.initial["version"] = self.instance.version
        self.initial["submission_id"] = self.instance.submission_id
        tz = ZoneInfo(self.instance.timezone)
        for key in ("starts_at", "ends_at"):
            value = getattr(self.instance, key)
            if value:
                self.initial[key] = value.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        if not can_publish:
            del self.fields["is_online"]
        if self.instance.series_id:
            for key in ("repeat_frequency", "repeat_interval", "repeat_count"):
                del self.fields[key]

    def clean(self):
        data = super().clean()
        try:
            zone = data.get("timezone") or self.instance.timezone
            validate_timezone(zone)
            tz = ZoneInfo(zone)
        except (ValidationError, ValueError):
            self.add_error("timezone", "Choisissez un fuseau IANA valide.")
            for key in ("starts_at", "ends_at"):
                if key in data:
                    self.add_error(key, "Corrigez le fuseau avant de valider les heures.")
            return data
        for key in ("starts_at", "ends_at"):
            raw = data.get(key)
            if not raw:
                continue
            try:
                value = datetime.fromisoformat(raw)
                if value.tzinfo is not None:
                    raise ValueError
                first = value.replace(tzinfo=tz, fold=0)
                second = value.replace(tzinfo=tz, fold=1)
                if first.utcoffset() != second.utcoffset():
                    raise ValueError
                data[key] = first
            except (ValueError, TypeError):
                self.add_error(
                    key, "Choisissez une heure locale valide et non ambiguë dans ce fuseau."
                )
        self.instance.booking_resource_ids = [r.pk for r in data.get("resources", [])]
        if not self.errors and data.get("repeat_frequency"):
            candidate = Event(
                site=self.instance.site,
                **{key: data.get(key, getattr(self.instance, key)) for key in TEMPLATE_FIELDS},
            )
            try:
                if not self.editing_series and any(
                    not resource.is_active for resource in data["resources"]
                ):
                    raise ValidationError("Réactivez les ressources avant de créer une série.")
                dates = validate_rule(candidate, data)
                if not self.editing_series:
                    from .bookings import validate_event_occupation

                    for left, right in dates:
                        candidate.starts_at, candidate.ends_at = left, right
                        validate_event_occupation(candidate, self.instance.booking_resource_ids)
            except ValidationError as error:
                self.add_error("repeat_frequency", error)
        return data


class MoveForm(forms.Form):
    date = forms.DateField(label="Nouvelle date", widget=forms.DateInput(attrs={"type": "date"}))
    hour = forms.TimeField(label="Nouvelle heure", widget=forms.TimeInput(attrs={"type": "time"}))
    timezone = forms.CharField(
        label="Fuseau de saisie", max_length=64, validators=[validate_timezone]
    )
    version = forms.IntegerField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="Je confirme le déplacement de cette occurrence")

    def clean(self):
        data = super().clean()
        if all(data.get(key) is not None for key in ("date", "hour", "timezone")):
            try:
                data["new_start"] = local_instant(
                    datetime.combine(data["date"], data["hour"]), data["timezone"]
                )
                if not 1900 <= data["date"].year <= 2100:
                    self.add_error("date", "Choisissez une année entre 1900 et 2100.")
            except ValidationError as error:
                self.add_error("hour", error)
        return data


class SchedulerForm(forms.Form):
    date = forms.DateField(label="Date", widget=forms.DateInput(attrs={"type": "date"}))
    view = forms.ChoiceField(label="Vue", choices=VIEWS)
    timezone = forms.CharField(
        label="Fuseau d’affichage", max_length=64, validators=[validate_timezone]
    )
    kind = forms.ChoiceField(
        label="Type", required=False, choices=[("", "Tous les types"), *Event.Kind.choices]
    )
    resource = forms.ModelChoiceField(
        label="Ressource", required=False, queryset=Resource.objects.none()
    )
    q = forms.CharField(label="Recherche", max_length=200, required=False)

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resource"].queryset = Resource.objects.filter(site=site)

    def clean_date(self):
        value = self.cleaned_data["date"]
        if not 1900 <= value.year <= 2100:
            raise ValidationError("Choisissez une date entre 1900 et 2100.")
        return value


class ResourceForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Resource
        fields = ["name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["version"] = self.instance.version

    def clean_name(self):
        name = self.cleaned_data["name"]
        if (
            Resource.objects.filter(site=self.instance.site, name=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError("Une ressource porte déjà ce nom sur ce site.")
        return name


class LifecycleForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(label="Je confirme cette action")
