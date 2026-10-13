from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.db import models
from django.utils.dateparse import parse_date

from apps.tenancy.constants import OWNER_SITE_ID


def validate_reference(instance, field, key):
    if not getattr(instance, f"{field}_id"):
        return
    value = getattr(instance, field)
    lookup = value.lookup_list
    if (
        lookup.site_id != instance.site_id
        or lookup.module != "addressbook"
        or lookup.field_key != key
        or lookup.kind != ("category" if field == "category" else "choice")
        or value.addressbook_kind not in ("", instance.kind)
    ):
        raise ValidationError({field: "Choisissez une référence du même site et du bon champ."})
    if not value.is_active or not lookup.is_active:
        previous_id = (
            type(instance)
            .objects.filter(pk=instance.pk)
            .values_list(f"{field}_id", flat=True)
            .first()
            if instance.pk
            else None
        )
        if previous_id != value.pk:
            raise ValidationError({field: "Cette référence inactive ne peut plus être attribuée."})


def validate_child_reference(instance, field, key):
    reference_id = getattr(instance, f"{field}_id")
    if not reference_id or not instance.entry_id:
        return
    value = getattr(instance, field)
    lookup = value.lookup_list
    if (
        lookup.site_id != instance.entry.site_id
        or lookup.module != "addressbook"
        or lookup.kind != "choice"
        or lookup.field_key != key
        or value.addressbook_kind not in ("", instance.entry.kind)
    ):
        raise ValidationError({field: "Choisissez une référence du même site et de cette famille."})
    if not value.is_active or not lookup.is_active:
        previous_id = (
            type(instance)
            .objects.filter(pk=instance.pk)
            .values_list(f"{field}_id", flat=True)
            .first()
            if instance.pk
            else None
        )
        if previous_id != value.pk:
            raise ValidationError({field: "Cette référence inactive ne peut plus être attribuée."})


class Entry(models.Model):
    class Kind(models.TextChoices):
        PERSON = "person", "Personne"
        COMPANY = "company", "Entreprise"
        ASSOCIATION = "association", "Association"

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT, related_name="addressbook")
    kind = models.CharField("type de fiche", max_length=16, choices=Kind.choices)
    entry_type = models.ForeignKey(
        "addressbook.EntryType",
        on_delete=models.PROTECT,
        related_name="entries",
        verbose_name="type détaillé",
    )
    name = models.CharField("nom / dénomination", max_length=200)
    first_name = models.CharField("prénom (personne)", max_length=100, blank=True)
    gender = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addressbook_genders",
        verbose_name="genre (personne)",
    )
    salutation = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addressbook_salutations",
        verbose_name="civilité (personne)",
    )
    reference = models.CharField("référence", max_length=64, blank=True)
    category = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addressbook_categories",
        verbose_name="catégorie",
    )
    legal_form = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addressbook_legal_forms",
        verbose_name="forme juridique (organisation)",
    )
    origin = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addressbook_origins",
        verbose_name="origine",
    )
    notes = models.TextField("notes internes", max_length=10000, blank=True)
    is_active = models.BooleanField("active", default=True)
    archived_at = models.DateTimeField("archivage", null=True, editable=False)
    created_at = models.DateTimeField("création", auto_now_add=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name="created_addressbook_entries",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        editable=False,
        related_name="updated_addressbook_entries",
    )

    class Meta:
        ordering = ["name", "first_name", "pk"]
        verbose_name = "fiche du carnet"
        verbose_name_plural = "fiches du carnet"
        permissions = [("restore_entry", "Peut restaurer les fiches du carnet")]
        indexes = [models.Index(fields=["site", "archived_at", "kind", "name"])]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(site_id=OWNER_SITE_ID), name="adr_client_site"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["person", "company", "association"]),
                name="adr_entry_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="person") | models.Q(first_name=""),
                name="adr_person_first_name",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="person") | models.Q(legal_form__isnull=True),
                name="adr_org_legal_form",
            ),
        ]

    def clean(self):
        super().clean()
        if self.site_id == OWNER_SITE_ID:
            raise ValidationError("Le site réservé ne reçoit pas de fiches client.")
        if self.kind != self.Kind.PERSON and self.first_name:
            raise ValidationError({"first_name": "Le prénom est réservé aux personnes."})
        if self.kind == self.Kind.PERSON and self.legal_form_id:
            raise ValidationError({"legal_form": "La forme juridique concerne les organisations."})
        if self.kind != self.Kind.PERSON:
            person_only_errors = {}
            if self.gender_id:
                person_only_errors["gender"] = "Le genre est réservé aux personnes."
            if self.salutation_id:
                person_only_errors["salutation"] = "La civilité est réservée aux personnes."
            if person_only_errors:
                raise ValidationError(person_only_errors)
        if not self.entry_type_id:
            raise ValidationError({"entry_type": "Choisissez un type de fiche."})
        if self.entry_type.site_id != self.site_id or self.entry_type.kind != self.kind:
            raise ValidationError(
                {"entry_type": "Choisissez un type de cette famille et de ce site."}
            )
        if not self.entry_type.is_active:
            previous_type_id = (
                Entry.objects.filter(pk=self.pk).values_list("entry_type_id", flat=True).first()
                if self.pk
                else None
            )
            if previous_type_id != self.entry_type_id:
                raise ValidationError({"entry_type": "Ce type inactif ne peut plus être attribué."})
        for field in ("category", "gender", "salutation", "legal_form", "origin"):
            validate_reference(self, field, f"addressbook_entry.{field}")
        if (
            self.pk
            and (not self.is_active or self.archived_at)
            and self.people.filter(ends_on__isnull=True).exists()
        ):
            raise ValidationError(
                "Clôturez les rattachements avant de désactiver ou archiver l’organisation."
            )
        if self.pk:
            previous = Entry.objects.get(pk=self.pk)
            if previous.kind != self.kind and (self.employments.exists() or self.people.exists()):
                raise ValidationError(
                    {"kind": "Le type ne peut changer avec des rattachements conservés."}
                )

    def __str__(self):
        return f"{self.name} {self.first_name}".strip()


class EntryType(models.Model):
    """Site-local subtype; Entry.kind remains the stable relationship family."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.PROTECT, related_name="addressbook_types"
    )
    kind = models.CharField("famille", max_length=16, choices=Entry.Kind.choices)
    code = models.SlugField("code stable", max_length=64)
    label = models.CharField("libellé", max_length=100)
    is_active = models.BooleanField("actif", default=True)

    class Meta:
        ordering = ["kind", "label", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="adr_site_type_code"),
            models.CheckConstraint(
                condition=~models.Q(site_id=OWNER_SITE_ID), name="adr_type_client_site"
            ),
        ]
        verbose_name = "type de fiche du carnet"
        verbose_name_plural = "types de fiche du carnet"

    def clean(self):
        super().clean()
        if self.site_id == OWNER_SITE_ID:
            raise ValidationError("Le site réservé ne reçoit pas de types client.")
        if self.pk:
            previous = EntryType.objects.get(pk=self.pk)
            if (self.site_id, self.kind, self.code) != (
                previous.site_id,
                previous.kind,
                previous.code,
            ):
                raise ValidationError("Le site, la famille et le code d’un type sont immuables.")

    def __str__(self):
        return f"{self.get_kind_display()} — {self.label}"


class ContactPoint(models.Model):
    class Mode(models.TextChoices):
        EMAIL = "email", "Courriel"
        WORK_EMAIL = "work_email", "Courriel professionnel"
        PHONE = "phone", "Téléphone"
        MOBILE = "mobile", "Mobile"
        WORK_PHONE = "work_phone", "Téléphone professionnel"
        WEBSITE = "website", "Site web"

    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="contacts")
    mode = models.CharField("mode", max_length=16, choices=Mode.choices)
    label = models.CharField("libellé", max_length=100, blank=True)
    value = models.CharField("coordonnée", max_length=254)
    is_active = models.BooleanField("active", default=True)
    is_primary = models.BooleanField("principale pour ce mode", default=False)

    class Meta:
        ordering = ["mode", "-is_primary", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "mode"],
                condition=models.Q(is_primary=True),
                name="adr_primary_contact_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(is_primary=False) | models.Q(is_active=True),
                name="adr_active_primary_contact",
            ),
        ]

    def clean(self):
        super().clean()
        self.value = self.value.strip()
        if self.mode in (self.Mode.EMAIL, self.Mode.WORK_EMAIL):
            validate_email(self.value)
            local, domain = self.value.rsplit("@", 1)
            self.value = f"{local}@{domain.lower()}"
        elif self.mode == self.Mode.WEBSITE:
            URLValidator(schemes=["http", "https"])(self.value)
            if not self.value.lower().startswith(("https://", "http://")):
                raise ValidationError({"value": "Utilisez une URL HTTP ou HTTPS."})
        elif not (7 <= sum(c.isdigit() for c in self.value) <= 15) or any(
            c not in "+0123456789 ()-." for c in self.value
        ):
            raise ValidationError({"value": "Indiquez un téléphone de 7 à 15 chiffres."})
        if self.is_primary and not self.is_active:
            raise ValidationError(
                {"is_primary": "Une coordonnée inactive ne peut être principale."}
            )


class Address(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="addresses")
    address_type = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        related_name="addressbook_addresses",
        verbose_name="type d’adresse",
    )
    label = models.CharField("libellé", max_length=100, blank=True)
    street = models.CharField("adresse", max_length=300)
    postal_code = models.CharField("code postal", max_length=20, blank=True)
    city = models.CharField("ville", max_length=100)
    country = models.CharField("pays", max_length=100)
    is_active = models.BooleanField("active", default=True)
    is_primary = models.BooleanField("adresse principale", default=False)

    class Meta:
        ordering = ["-is_primary", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry"], condition=models.Q(is_primary=True), name="adr_primary_address"
            ),
            models.CheckConstraint(
                condition=models.Q(is_primary=False) | models.Q(is_active=True),
                name="adr_active_primary_address",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.address_type_id:
            raise ValidationError({"address_type": "Choisissez un type d’adresse."})
        validate_child_reference(self, "address_type", "addressbook_address.address_type")
        if self.is_primary and not self.is_active:
            raise ValidationError({"is_primary": "Une adresse inactive ne peut être principale."})
        if (
            self.pk
            and not self.is_active
            and self.employments.filter(ends_on__isnull=True).exists()
        ):
            raise ValidationError(
                {"is_active": "Clôturez les rattachements utilisant cette adresse."}
            )

    def __str__(self):
        return f"{self.label or self.street} — {self.city}"


class Employment(models.Model):
    person = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="employments")
    organization = models.ForeignKey(
        Entry, on_delete=models.PROTECT, related_name="people", verbose_name="organisation"
    )
    address = models.ForeignKey(
        Address,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employments",
        verbose_name="adresse de l’organisation",
    )
    function = models.CharField("fonction", max_length=100)
    function_type = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="addressbook_employments",
        verbose_name="fonction de référence",
    )
    starts_on = models.DateField("début", null=True, blank=True)
    ends_on = models.DateField("fin", null=True, blank=True)

    class Meta:
        ordering = ["-starts_on", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(person=models.F("organization")), name="adr_no_self_employment"
            ),
            models.CheckConstraint(
                condition=models.Q(starts_on__isnull=True)
                | models.Q(ends_on__isnull=True)
                | models.Q(ends_on__gte=models.F("starts_on")),
                name="adr_employment_dates",
            ),
        ]

    def clean(self):
        super().clean()
        if self.function_type_id and self.person_id:
            lookup = self.function_type.lookup_list
            if (
                lookup.site_id != self.person.site_id
                or lookup.module != "addressbook"
                or lookup.kind != "choice"
                or lookup.field_key != "addressbook_employment.function_type"
                or self.function_type.addressbook_kind not in ("", Entry.Kind.PERSON)
            ):
                raise ValidationError({"function_type": "Choisissez une fonction de ce site."})
            if not self.function_type.is_active or not lookup.is_active:
                previous_type_id = (
                    Employment.objects.filter(pk=self.pk)
                    .values_list("function_type_id", flat=True)
                    .first()
                    if self.pk
                    else None
                )
                if previous_type_id != self.function_type_id:
                    raise ValidationError(
                        {"function_type": "Cette fonction inactive ne peut plus être attribuée."}
                    )
        if self.person_id and self.person.kind != Entry.Kind.PERSON:
            raise ValidationError("Le rattachement doit partir d’une personne.")
        if self.organization_id and self.person_id:
            if (
                self.organization.site_id != self.person.site_id
                or self.organization.kind == Entry.Kind.PERSON
            ):
                raise ValidationError({"organization": "Choisissez une organisation du même site."})
            if not self.ends_on and (
                not self.organization.is_active or self.organization.archived_at
            ):
                raise ValidationError(
                    {"organization": "Clôturez le rattachement vers cette organisation inactive."}
                )
        if self.address_id and self.address.entry_id != self.organization_id:
            raise ValidationError(
                {"address": "L’adresse doit appartenir à l’organisation choisie."}
            )
        if self.address_id and not self.ends_on and not self.address.is_active:
            raise ValidationError(
                {"address": "L’adresse doit être active pour un rattachement ouvert."}
            )
        if self.pk:
            previous = Employment.objects.get(pk=self.pk)
            if (
                previous.organization_id != self.organization_id
                or previous.function != self.function
            ):
                raise ValidationError(
                    "Pour changer d’organisation ou de fonction, clôturez ce rattachement "
                    "et créez-en un nouveau afin de conserver l’historique."
                )
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "La fin doit suivre le début."})


class ExtraInfo(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="extra_info")
    label = models.CharField("libellé", max_length=100)
    info_type = models.ForeignKey(
        "configuration.LookupEntry",
        on_delete=models.PROTECT,
        related_name="addressbook_extra_info",
        verbose_name="catégorie d’information",
    )
    value = models.CharField("valeur privée", max_length=1000)
    is_active = models.BooleanField("active", default=True)

    class Meta:
        ordering = ["label", "pk"]

    def clean(self):
        super().clean()
        if not self.info_type_id:
            raise ValidationError({"info_type": "Choisissez une catégorie d’information."})
        validate_child_reference(self, "info_type", "addressbook_extrainfo.info_type")
        if self.info_type.value_kind not in ("text", "date", "number", "boolean"):
            raise ValidationError({"info_type": "Cette catégorie n’a pas de format valide."})
        valid = True
        if self.info_type.value_kind == "date":
            try:
                parsed = parse_date(self.value)
                valid = parsed is not None and parsed.isoformat() == self.value
            except ValueError:
                valid = False
        elif self.info_type.value_kind == "number":
            try:
                valid = Decimal(self.value).is_finite()
            except InvalidOperation:
                valid = False
        elif self.info_type.value_kind == "boolean":
            valid = self.value.lower() in ("oui", "non")
        if not valid:
            raise ValidationError({"value": "La valeur ne correspond pas au type choisi."})
