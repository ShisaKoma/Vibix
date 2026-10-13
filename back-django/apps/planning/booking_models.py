"""Réservations internes : les ressources Planning restent l'autorité d'occupation."""

from uuid import uuid4

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def same_site(instance, fields):
    errors = {}
    if instance.site_id == 1:
        errors["site"] = "Le site réservé ne reçoit pas de réservations."
    for name in fields:
        if not getattr(instance, name + "_id", None):
            continue
        related = getattr(instance, name)
        related_site = related.product.site_id if name == "variant" else related.site_id
        if related_site != instance.site_id:
            errors[name] = "Choisissez un objet du même site."
    return errors


class BookingCalendar(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    name = models.CharField("nom", max_length=120)
    timezone = models.CharField("fuseau IANA", max_length=64)
    is_active = models.BooleanField("actif", default=False)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = "planning de réservation"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(site_id=1), name="booking_calendar_client_site"
            )
        ]

    def clean(self):
        from .models import validate_timezone

        super().clean()
        validate_timezone(self.timezone)
        if errors := same_site(self, []):
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class BookingWindow(models.Model):
    calendar = models.ForeignKey(BookingCalendar, on_delete=models.PROTECT, related_name="windows")
    resource = models.ForeignKey("planning.Resource", on_delete=models.PROTECT)
    starts_at = models.DateTimeField("début")
    ends_at = models.DateTimeField("fin")
    is_closed = models.BooleanField("fermeture / indisponibilité", default=False)
    is_active = models.BooleanField("active", default=True)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["starts_at", "pk"]
        verbose_name = "plage de disponibilité"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="booking_window_duration",
            )
        ]
        indexes = [models.Index(fields=["calendar", "resource", "starts_at", "ends_at"])]

    def clean(self):
        super().clean()
        errors = {}
        if self.calendar_id and self.resource_id and self.calendar.site_id != self.resource.site_id:
            errors["resource"] = "La ressource doit appartenir au site du planning."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "La fin doit être strictement postérieure au début."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.resource} — {self.starts_at}"


class BookingOffer(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    calendar = models.ForeignKey(BookingCalendar, on_delete=models.PROTECT)
    resource = models.ForeignKey("planning.Resource", on_delete=models.PROTECT)
    variant = models.ForeignKey(
        "products.Variant", on_delete=models.PROTECT, related_name="booking_offers"
    )
    tariff = models.ForeignKey(
        "products.Tariff",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="booking_offers",
    )
    title = models.CharField("libellé de l'offre", max_length=200)
    duration_minutes = models.PositiveIntegerField(
        "durée en minutes", validators=[MinValueValidator(1), MaxValueValidator(1440)]
    )
    step_minutes = models.PositiveIntegerField(
        "pas des créneaux en minutes", validators=[MinValueValidator(1), MaxValueValidator(1440)]
    )
    before_minutes = models.PositiveIntegerField(
        "marge avant en minutes", validators=[MaxValueValidator(1440)]
    )
    after_minutes = models.PositiveIntegerField(
        "marge après en minutes", validators=[MaxValueValidator(1440)]
    )
    lead_minutes = models.PositiveIntegerField(
        "délai minimum en minutes", validators=[MaxValueValidator(525600)]
    )
    horizon_days = models.PositiveIntegerField(
        "horizon maximum en jours", validators=[MinValueValidator(1), MaxValueValidator(365)]
    )
    cancellation_minutes = models.PositiveIntegerField(
        "préavis d'annulation en minutes", validators=[MaxValueValidator(525600)]
    )
    cancellation_terms = models.TextField("règles d'annulation", max_length=5000)
    recipient = models.EmailField("destinataire interne des notifications")
    is_active = models.BooleanField("ouvrir les réservations internes", default=False)
    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["title", "pk"]
        verbose_name = "offre de réservation"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(site_id=1), name="booking_offer_client_site"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    duration_minutes__gte=1,
                    duration_minutes__lte=1440,
                    step_minutes__gte=1,
                    step_minutes__lte=1440,
                    before_minutes__lte=1440,
                    after_minutes__lte=1440,
                    lead_minutes__lte=525600,
                    horizon_days__gte=1,
                    horizon_days__lte=365,
                    cancellation_minutes__lte=525600,
                ),
                name="booking_offer_bounds",
            ),
        ]

    def clean(self):
        super().clean()
        errors = same_site(self, ["calendar", "resource", "variant"])
        if self.tariff_id and self.tariff.variant_id != self.variant_id:
            errors["tariff"] = "Le tarif doit appartenir à la déclinaison choisie."
        if self.pk:
            previous = BookingOffer.objects.get(pk=self.pk)
            if self.bookings.exists() and previous.variant_id != self.variant_id:
                errors["variant"] = "La déclinaison d'une offre utilisée est immuable."
            if self.bookings.exists():
                protected = [
                    "calendar",
                    "resource",
                    "duration_minutes",
                    "before_minutes",
                    "after_minutes",
                ]
                if any(
                    getattr(previous, key + "_id" if key in ("calendar", "resource") else key)
                    != getattr(self, key + "_id" if key in ("calendar", "resource") else key)
                    for key in protected
                ):
                    errors["resource"] = (
                        "Des réservations existent : créez une nouvelle offre pour changer "
                        "planning, ressource, durée ou marges."
                    )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title


class Booking(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "En attente — non occupante"
        CONFIRMED = "confirmed", "Confirmée"
        REFUSED = "refused", "Refusée"
        CANCELLED = "cancelled", "Annulée"
        COMPLETED = "completed", "Terminée"
        NO_SHOW = "no_show", "Absence"

    OCCUPYING = ["confirmed", "completed", "no_show"]
    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    offer = models.ForeignKey(BookingOffer, on_delete=models.PROTECT, related_name="bookings")
    resource = models.ForeignKey(
        "planning.Resource", on_delete=models.PROTECT, related_name="bookings"
    )
    reference = models.UUIDField(default=uuid4, unique=True, editable=False)
    submission_id = models.UUIDField(editable=False)
    payload_hash = models.CharField(max_length=64, editable=False)
    name = models.CharField("nom du demandeur", max_length=200)
    email = models.EmailField("courriel", blank=True)
    phone = models.CharField("téléphone", blank=True, max_length=32)
    starts_at = models.DateTimeField("début")
    ends_at = models.DateTimeField("fin")
    occupied_starts_at = models.DateTimeField(editable=False)
    occupied_ends_at = models.DateTimeField(editable=False)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    snapshot = models.JSONField(editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at", "-pk"]
        verbose_name = "réservation interne"
        default_permissions = ("view", "add")
        permissions = [
            ("process_booking", "Peut confirmer/refuser/terminer les réservations"),
            ("move_booking", "Peut déplacer les réservations"),
            ("cancel_booking", "Peut annuler les réservations"),
            ("view_booking_personal_data", "Peut consulter les coordonnées des demandeurs"),
            ("retry_booking_notification", "Peut relancer les notifications Réservations"),
        ]
        constraints = [
            models.CheckConstraint(condition=~models.Q(site_id=1), name="booking_client_site"),
            ExclusionConstraint(
                name="booking_resource_no_overlap",
                expressions=[
                    ("resource", RangeOperators.EQUAL),
                    (
                        models.Func(
                            models.F("occupied_starts_at"),
                            models.F("occupied_ends_at"),
                            models.Value("[)"),
                            function="TSTZRANGE",
                            output_field=DateTimeRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(state__in=["confirmed", "completed", "no_show"]),
            ),
            models.UniqueConstraint(
                fields=["site", "submission_id"], name="booking_site_submission"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "pending",
                        "confirmed",
                        "refused",
                        "cancelled",
                        "completed",
                        "no_show",
                    ]
                ),
                name="booking_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    ends_at__gt=models.F("starts_at"),
                    occupied_starts_at__lte=models.F("starts_at"),
                    occupied_ends_at__gte=models.F("ends_at"),
                ),
                name="booking_valid_interval",
            ),
        ]
        indexes = [
            models.Index(fields=["site", "state", "starts_at"]),
            models.Index(fields=["resource", "occupied_starts_at", "occupied_ends_at"]),
        ]

    def clean(self):
        super().clean()
        errors = same_site(self, ["offer", "resource"])
        if self.offer_id and self.resource_id and self.offer.resource_id != self.resource_id:
            errors["resource"] = "La ressource doit correspondre à celle de l'offre."
        for field in ("name", "email", "phone"):
            if any(ord(char) < 32 or ord(char) == 127 for char in getattr(self, field)):
                errors[field] = "Les caractères de contrôle sont interdits."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "La fin doit être postérieure au début."
        if errors:
            raise ValidationError(errors)


class BookingHistory(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="history")
    actor = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=32)
    reason = models.CharField("motif", max_length=1000, blank=True)
    details = models.JSONField(default=dict, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class BookingNotification(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="notifications")
    recipient = models.EmailField()
    event_version = models.PositiveIntegerField()
    state = models.CharField(
        max_length=16,
        choices=[
            ("pending", "En attente"),
            ("processing", "Envoi en cours"),
            ("sent", "Envoyée"),
            ("failed", "Échec"),
        ],
        default="pending",
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease = models.UUIDField(null=True)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "event_version"], name="booking_notification_version"
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=["pending", "processing", "sent", "failed"]),
                name="booking_notification_state",
            ),
        ]
        indexes = [models.Index(fields=["state", "next_attempt_at"])]
