from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator
from django.db import models


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValidationError(
            "Choisissez un fuseau IANA valide, par exemple Europe/Paris."
        ) from error


class Resource(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    name = models.CharField("nom", max_length=120)
    is_active = models.BooleanField("active", default=True)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "name"], name="planning_resource_site_name")
        ]
        verbose_name = "ressource Planning"

    def clean(self):
        if self.site_id == 1:
            raise ValidationError("Le site réservé ne reçoit pas de ressources client.")

    def __str__(self):
        return self.name


class EventSeries(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Chaque jour"
        WEEKLY = "weekly", "Chaque semaine"
        MONTHLY = "monthly", "Chaque mois"

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    frequency = models.CharField(max_length=12, choices=Frequency.choices)
    interval = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    count = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(2), MaxValueValidator(100)]
    )
    template = models.JSONField()
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(interval__gte=1, interval__lte=12),
                name="planning_series_interval",
            ),
            models.CheckConstraint(
                condition=models.Q(count__gte=2, count__lte=100), name="planning_series_count"
            ),
        ]

    def clean(self):
        if self.site_id == 1:
            raise ValidationError("Le site réservé ne reçoit pas de séries client.")


class Event(models.Model):
    class Kind(models.TextChoices):
        EVENT = "event", "Événement"
        CONCERT = "concert", "Concert"
        WEBINAR = "webinar", "Webinaire"

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    series = models.ForeignKey(
        EventSeries,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="events",
        editable=False,
    )
    occurrence_index = models.PositiveSmallIntegerField(null=True, editable=False)
    is_exception = models.BooleanField(default=False, editable=False)
    title = models.CharField("titre", max_length=200)
    kind = models.CharField("type", max_length=12, choices=Kind.choices, default=Kind.EVENT)
    description = models.TextField("description", max_length=10000, blank=True)
    location = models.CharField("lieu / plateforme", max_length=300, blank=True)
    link = models.URLField(
        "lien public",
        max_length=1000,
        blank=True,
        validators=[URLValidator(schemes=["http", "https"])],
    )
    starts_at = models.DateTimeField("début")
    ends_at = models.DateTimeField("fin")
    timezone = models.CharField(
        "fuseau", max_length=64, default="Europe/Paris", validators=[validate_timezone]
    )
    all_day = models.BooleanField("journée entière", default=False)
    resources = models.ManyToManyField(Resource, blank=True, verbose_name="ressources")
    is_online = models.BooleanField("publier sur le site", default=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    submission_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "pk"]
        permissions = [
            ("publish_event", "Peut publier les événements"),
            ("restore_event", "Peut restaurer les événements"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "occurrence_index"], name="planning_series_occurrence"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(series__isnull=True, occurrence_index__isnull=True)
                    | models.Q(series__isnull=False, occurrence_index__isnull=False)
                ),
                name="planning_event_series_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="planning_event_positive_duration",
            ),
        ]
        indexes = [models.Index(fields=["site", "starts_at", "ends_at"])]
        verbose_name = "événement"

    def clean(self):
        super().clean()
        errors = {}
        if self.site_id == 1:
            errors["site"] = "Le site réservé ne reçoit pas d’événements client."
        if self.series_id and self.series.site_id != self.site_id:
            errors["series"] = "La série doit appartenir au même site."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "La fin doit être postérieure au début."
        validate_timezone(self.timezone)
        if self.all_day and self.starts_at and self.ends_at:
            tz = ZoneInfo(self.timezone)
            if any(
                value.astimezone(tz).time().isoformat() != "00:00:00"
                for value in (self.starts_at, self.ends_at)
            ):
                errors["all_day"] = (
                    "Choisissez minuit au début et minuit après la dernière journée."
                )
        if errors:
            raise ValidationError(errors)
        if not getattr(self, "skip_booking_occupation", False):
            from .bookings import validate_event_occupation

            validate_event_occupation(self, getattr(self, "booking_resource_ids", None))

    def __str__(self):
        return self.title


# Les modèles de réservation sont séparés pour garder les événements éditoriaux lisibles.
from .booking_models import (  # noqa: E402,F401
    Booking,
    BookingCalendar,
    BookingHistory,
    BookingNotification,
    BookingOffer,
    BookingWindow,
)
