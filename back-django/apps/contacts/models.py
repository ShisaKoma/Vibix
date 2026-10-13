import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.tenancy.constants import OWNER_SITE_ID

QUESTION_KEYS = tuple(f"question_{number:02d}" for number in range(1, 11))


def validate_questions(value):
    if not isinstance(value, list) or len(value) > 10:
        raise ValidationError("Configurez au maximum dix questions.")
    keys = set()
    for question in value:
        if (
            not isinstance(question, dict)
            or set(question) != {"key", "label", "required"}
            or question.get("key") not in QUESTION_KEYS
            or question["key"] in keys
            or not isinstance(question.get("label"), str)
            or not 1 <= len(question["label"].strip()) <= 200
            or any(ord(char) < 32 or ord(char) == 127 for char in question["label"])
            or not isinstance(question.get("required"), bool)
        ):
            raise ValidationError("Configuration de question invalide.")
        keys.add(question["key"])


class ContactFormDefinition(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.CASCADE, related_name="contact_forms")
    code = models.CharField(
        "Code du type de formulaire",
        max_length=64,
        validators=[
            RegexValidator(
                r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$",
                "Utilisez des lettres minuscules, chiffres, tirets ou soulignés.",
            )
        ],
    )
    label = models.CharField("Libellé du formulaire", max_length=200)
    is_active = models.BooleanField("Actif", default=True)
    position = models.PositiveSmallIntegerField("Ordre", default=0)
    questions = models.JSONField(default=list, blank=True, validators=[validate_questions])
    question_settings = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "label", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="contact_form_site_code_unique"),
            models.CheckConstraint(
                condition=~models.Q(site_id=OWNER_SITE_ID), name="contact_form_client_site"
            ),
        ]
        verbose_name = "type de formulaire Contact"

    def clean(self):
        super().clean()
        if self.site_id == OWNER_SITE_ID:
            raise ValidationError("Le site réservé ne reçoit pas de formulaires client.")
        if not self.pk and type(self).objects.filter(site_id=self.site_id).count() >= 100:
            raise ValidationError("Un site peut configurer au maximum cent formulaires.")
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values("site_id", "code").first()
            if previous and (previous["site_id"] != self.site_id or previous["code"] != self.code):
                raise ValidationError("Le site et le code d’un formulaire existant sont immuables.")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.label):
            raise ValidationError({"label": "Les caractères de contrôle sont interdits."})
        validate_questions(self.questions)
        if not isinstance(self.question_settings, dict) or set(self.question_settings) - set(
            QUESTION_KEYS
        ):
            raise ValidationError("Paramétrage de questions invalide.")
        active_questions = []
        for key in QUESTION_KEYS:
            setting = self.question_settings.get(key)
            if setting is None:
                continue
            if (
                not isinstance(setting, dict)
                or set(setting) != {"active", "label", "required"}
                or not isinstance(setting["active"], bool)
                or not isinstance(setting["required"], bool)
                or not isinstance(setting["label"], str)
                or len(setting["label"]) > 200
                or any(ord(char) < 32 or ord(char) == 127 for char in setting["label"])
            ):
                raise ValidationError("Paramétrage de question invalide.")
            if setting["active"]:
                active_questions.append(
                    {"key": key, "label": setting["label"], "required": setting["required"]}
                )
        if active_questions != self.questions:
            raise ValidationError("Les questions actives doivent correspondre à leur paramétrage.")

    def snapshot(self):
        return {
            "code": self.code,
            "label": self.label,
            "version": self.updated_at.isoformat(),
            "questions": self.questions,
        }


class ContactRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Nouvelle"
        SPAM = "spam", "Suspecte / spam"
        ACCEPTED = "accepted", "Validée"
        IN_PROGRESS = "in_progress", "En cours"
        CLOSED = "closed", "Clôturée"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.PROTECT, related_name="contact_requests"
    )
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    receipt_hash = models.CharField(max_length=64, editable=False)
    payload_hash = models.CharField(max_length=64, editable=False)
    name = models.CharField("Nom", max_length=200)
    first_name = models.CharField("Prénom", max_length=100, blank=True)
    email = models.EmailField("Courriel")
    phone = models.CharField("Téléphone", max_length=32, blank=True)
    organization = models.CharField("Organisation", max_length=200, blank=True)
    title = models.CharField("Objet", max_length=200)
    message = models.TextField("Message", max_length=50000)
    form_type = models.CharField("Type de formulaire", max_length=64, blank=True)
    form_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    answers = models.JSONField(default=dict, blank=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    category = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_requests",
        verbose_name="Catégorie",
    )
    source = models.CharField(
        max_length=16, choices=[("web", "Formulaire public"), ("api", "Client API")]
    )
    api_client = models.ForeignKey(
        "contacts.APIClient", on_delete=models.SET_NULL, null=True, editable=False
    )
    addressbook_entry = models.ForeignKey(
        "addressbook.Entry", on_delete=models.SET_NULL, null=True, blank=True, editable=False
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "demande de contact"
        permissions = [
            ("restore_contactrequest", "Peut restaurer les demandes de contact"),
            ("link_addressbook", "Peut lier les demandes au carnet d’adresses"),
            ("manage_api_clients", "Peut gérer les clients API Contacts du site"),
            ("forward_contactrequest", "Peut retransmettre les demandes de contact par email"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "receipt_hash"], name="contact_site_receipt_unique"
            ),
            models.CheckConstraint(
                condition=~models.Q(site_id=OWNER_SITE_ID), name="contact_client_site"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["new", "spam", "accepted", "in_progress", "closed"]),
                name="contact_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=["web", "api"]), name="contact_source_valid"
            ),
        ]
        indexes = [models.Index(fields=["site", "status", "deleted_at", "created_at"])]

    def clean(self):
        super().clean()
        if self.site_id == OWNER_SITE_ID:
            raise ValidationError("Le site réservé ne reçoit pas de demandes client.")
        for field in ("name", "first_name", "email", "phone", "organization", "title"):
            if any(ord(char) < 32 or ord(char) == 127 for char in getattr(self, field)):
                raise ValidationError({field: "Les caractères de contrôle sont interdits."})
        if self.form_snapshot:
            snapshot = self.form_snapshot
            if not isinstance(snapshot, dict) or set(snapshot) != {
                "code",
                "label",
                "version",
                "questions",
            }:
                raise ValidationError("Instantané de formulaire invalide.")
            if snapshot["code"] != self.form_type or not all(
                isinstance(snapshot[key], str) for key in ("code", "label", "version")
            ):
                raise ValidationError("Identité de formulaire invalide.")
            validate_questions(snapshot["questions"])
            keys = {question["key"] for question in snapshot["questions"]}
            if (
                not isinstance(self.answers, dict)
                or set(self.answers) != keys
                or any(
                    not isinstance(value, str) or len(value) > 2000 or "\x00" in value
                    for value in self.answers.values()
                )
            ):
                raise ValidationError("Réponses complémentaires invalides.")
            if any(
                question["required"] and not self.answers[question["key"]].strip()
                for question in snapshot["questions"]
            ):
                raise ValidationError("Une question obligatoire est sans réponse.")
        elif self.form_type or self.answers:
            raise ValidationError("Les réponses doivent conserver leur formulaire d’origine.")
        if self.addressbook_entry_id and self.addressbook_entry.site_id != self.site_id:
            raise ValidationError("La fiche carnet doit appartenir au même site.")
        if self.api_client_id and self.api_client.site_id != self.site_id:
            raise ValidationError("Le client API doit appartenir au même site.")
        if self.category_id:
            lookup = self.category.lookup_list
            if (
                lookup.site_id != self.site_id
                or lookup.module != "contacts"
                or lookup.kind != "category"
                or lookup.field_key != "contacts_contactrequest.category"
            ):
                raise ValidationError({"category": "Choisissez une catégorie Contacts de ce site."})


class ContactNote(models.Model):
    contact = models.ForeignKey(ContactRequest, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    text = models.TextField("Note interne", max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class APIClient(models.Model):
    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.PROTECT, related_name="contact_api_clients"
    )
    name = models.CharField("Nom du client", max_length=100)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(site_id=OWNER_SITE_ID), name="contact_api_client_site"
            ),
            models.UniqueConstraint(
                models.F("site"), Lower("name"), name="contact_client_name_unique"
            ),
        ]


class QuotaBucket(models.Model):
    site = models.ForeignKey("tenancy.Site", on_delete=models.CASCADE)
    scope = models.CharField(max_length=16)
    identity = models.CharField(max_length=64)
    hour = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "scope", "identity", "hour"], name="contact_quota_unique"
            )
        ]
        indexes = [models.Index(fields=["hour"])]


class Notification(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "En attente"
        PROCESSING = "processing", "Envoi en cours"
        SENT = "sent", "Envoyée"
        FAILED = "failed", "Échec — à relancer"
        CANCELLED = "cancelled", "Suspendue"

    contact = models.OneToOneField(
        ContactRequest, on_delete=models.CASCADE, related_name="notification"
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease = models.UUIDField(null=True)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["state", "next_attempt_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["pending", "processing", "sent", "failed", "cancelled"]
                ),
                name="contact_notification_state_valid",
            )
        ]


class Forward(models.Model):
    """Explicit private retransmission; snapshot excludes treatment notes and SMTP secrets."""

    contact = models.ForeignKey(ContactRequest, on_delete=models.CASCADE, related_name="forwards")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    submission_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recipient = models.EmailField("Destinataire")
    body = models.TextField(editable=False)
    payload_hash = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=16, choices=Notification.State.choices, default="pending")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease = models.UUIDField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["state", "next_attempt_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=Notification.State.values),
                name="contact_forward_state_valid",
            )
        ]
