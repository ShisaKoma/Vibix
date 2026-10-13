from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

MODULE_CHOICES = [
    ("addressbook", "Carnet d’adresses"),
    ("articles", "Articles"),
    ("achievements", "Réalisations"),
    ("directories", "Répertoires"),
    ("products", "Produits"),
    ("projects", "Projets"),
    ("call_tenders", "Appels d’offres"),
    ("plannings", "Plannings"),
    ("appointments", "Rendez-vous"),
    ("cobweb", "Cobweb"),
    ("contacts", "Contacts"),
    ("careers", "Carrières"),
    ("team", "Trombinoscope"),
    ("pages", "Pages dynamiques"),
    ("newsletters", "Newsletters"),
    ("api", "API et intégrations"),
]

LIST_KIND_CHOICES = [
    ("category", "Catégories"),
    ("choice", "Listes déroulantes"),
]

# A reference list is meaningful only when the business field that consumes it is
# explicit. Empty is kept temporarily for pre-existing, generic lists.
FIELD_CHOICES = [
    ("products_product.category", "Produits — produit — catégorie"),
    ("products_product.language", "Produits — produit — langue"),
    ("contacts_contactrequest.category", "Contacts — demande — catégorie"),
    ("api_client.template", "Front client — template"),
    ("api_client.color_scheme", "Front client — ambiance claire ou sombre"),
    ("addressbook_entry.category", "Carnet — fiche — catégorie"),
    ("addressbook_entry.gender", "Carnet — personne — genre"),
    ("addressbook_entry.salutation", "Carnet — personne — civilité"),
    ("addressbook_entry.legal_form", "Carnet — organisation — forme juridique"),
    ("addressbook_entry.origin", "Carnet — fiche — origine"),
    ("addressbook_address.address_type", "Carnet — adresse — type"),
    ("addressbook_extrainfo.info_type", "Carnet — information — catégorie et format"),
    ("addressbook_employment.function_type", "Carnet — rattachement — fonction"),
    ("pages_page.category", "Pages — page — catégorie"),
    ("pages_page.group", "Pages — page — groupe hiérarchique"),
    ("pages_page.language", "Pages — page — langue"),
    ("articles_article.category", "Articles — article — catégorie"),
    ("articles_article.language", "Articles — article — langue"),
    ("cobweb_item.category", "Cobweb — fiche — catégorie"),
    ("cobweb_group.category", "Cobweb — groupe — catégorie"),
    ("cobweb_item.kind", "Cobweb — fiche — type d’entrée"),
    ("cobweb_group.presentation", "Cobweb — groupe — présentation des éléments"),
]

ADDRESSBOOK_REFERENCE_RELATIONS = {
    "addressbook_entry.category": ("addressbook_categories", "kind"),
    "addressbook_entry.gender": ("addressbook_genders", "kind"),
    "addressbook_entry.salutation": ("addressbook_salutations", "kind"),
    "addressbook_entry.legal_form": ("addressbook_legal_forms", "kind"),
    "addressbook_entry.origin": ("addressbook_origins", "kind"),
    "addressbook_address.address_type": ("addressbook_addresses", "entry__kind"),
    "addressbook_extrainfo.info_type": ("addressbook_extra_info", "entry__kind"),
    "addressbook_employment.function_type": ("addressbook_employments", "person__kind"),
}


class LookupList(models.Model):
    """Site-local category tree or list of values for a future module form."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="lookup_lists", verbose_name="site"
    )
    module = models.CharField("module", max_length=32, choices=MODULE_CHOICES)
    kind = models.CharField("type de liste", max_length=16, choices=LIST_KIND_CHOICES)
    field_key = models.CharField(
        "champ métier cible", max_length=64, choices=FIELD_CHOICES, blank=True
    )
    code = models.SlugField("code technique", max_length=64)
    name = models.CharField("nom", max_length=100)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("création", auto_now_add=True)
    updated_at = models.DateTimeField("mise à jour", auto_now=True)

    class Meta:
        ordering = ["module", "kind", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "module", "kind", "code"], name="unique_site_lookup_list_code"
            )
        ]
        verbose_name = "référentiel"
        verbose_name_plural = "référentiels"

    def __str__(self):
        return f"{self.get_module_display()} — {self.name}"

    def clean(self):
        super().clean()
        if self.field_key:
            expected_kind = (
                "category" if self.field_key.endswith((".category", ".group")) else "choice"
            )
            expected_module = self.field_key.split("_", 1)[0]
            if self.module != expected_module or self.kind != expected_kind:
                raise ValidationError(
                    {"field_key": "Le champ cible ne correspond pas au module et au type de liste."}
                )


class LookupEntry(models.Model):
    lookup_list = models.ForeignKey(
        LookupList, on_delete=models.CASCADE, related_name="entries", verbose_name="liste"
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        verbose_name="catégorie parente",
    )
    value = models.SlugField("code technique", max_length=64)
    label = models.CharField("libellé", max_length=100)
    addressbook_kind = models.CharField(
        "famille du carnet",
        max_length=16,
        blank=True,
        choices=[
            ("", "Toutes les familles"),
            ("person", "Personne"),
            ("company", "Entreprise"),
            ("association", "Association"),
        ],
        default="",
        help_text="Pour le carnet uniquement ; vide autorise toutes les familles.",
    )
    value_kind = models.CharField(
        "format de la valeur",
        max_length=16,
        blank=True,
        choices=[
            ("", "Sans format associé"),
            ("text", "Texte"),
            ("date", "Date ISO"),
            ("number", "Nombre"),
            ("boolean", "Oui / non"),
        ],
        default="",
        help_text="Pour une catégorie d’information du carnet ; définit la validation "
        "de la valeur.",
    )
    position = models.PositiveIntegerField("ordre", default=0)
    is_active = models.BooleanField("active", default=True)

    class Meta:
        ordering = ["position", "label", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["lookup_list", "value"], name="unique_lookup_entry_value"
            ),
            models.CheckConstraint(
                condition=models.Q(addressbook_kind__in=["", "person", "company", "association"]),
                name="config_lookup_addressbook_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(value_kind__in=["", "text", "date", "number", "boolean"]),
                name="config_lookup_value_kind",
            ),
        ]
        verbose_name = "valeur de liste"
        verbose_name_plural = "valeurs de liste"

    def clean(self):
        super().clean()
        if (
            self.addressbook_kind
            and self.lookup_list_id
            and self.lookup_list.module != "addressbook"
        ):
            raise ValidationError(
                {"addressbook_kind": "Cette famille s'applique uniquement au carnet d’adresses."}
            )
        if self.lookup_list_id:
            is_extra_type = self.lookup_list.field_key == "addressbook_extrainfo.info_type"
            if is_extra_type and not self.value_kind:
                raise ValidationError({"value_kind": "Choisissez le format de cette information."})
            if not is_extra_type and self.value_kind:
                raise ValidationError(
                    {"value_kind": "Le format concerne uniquement les informations du carnet."}
                )
            if self.pk and is_extra_type and self.addressbook_extra_info.exists():
                previous_kind = (
                    LookupEntry.objects.filter(pk=self.pk)
                    .values_list("value_kind", flat=True)
                    .first()
                )
                if previous_kind != self.value_kind:
                    raise ValidationError(
                        {
                            "value_kind": "Créez une nouvelle catégorie pour changer "
                            "un format déjà utilisé."
                        }
                    )
            if self.pk and self.addressbook_kind:
                previous_scope = (
                    LookupEntry.objects.filter(pk=self.pk)
                    .values_list("addressbook_kind", flat=True)
                    .first()
                )
                relation = ADDRESSBOOK_REFERENCE_RELATIONS.get(self.lookup_list.field_key)
                if previous_scope != self.addressbook_kind and relation:
                    name, kind_path = relation
                    if getattr(self, name).exclude(**{kind_path: self.addressbook_kind}).exists():
                        raise ValidationError(
                            {
                                "addressbook_kind": "Cette valeur est déjà utilisée par "
                                "une autre famille de fiches."
                            }
                        )
        if self.lookup_list_id and self.lookup_list.field_key == "cobweb_group.presentation":
            if self.value not in ("cards", "list", "slideshow"):
                raise ValidationError(
                    {"value": "Utilisez cards (cartes), list (liste) ou slideshow (diaporama)."}
                )
        if (
            self.parent_id
            and self.lookup_list_id
            and self.parent.lookup_list_id != self.lookup_list_id
        ):
            raise ValidationError(
                {"parent": "La catégorie parente doit appartenir à la même liste."}
            )
        if self.parent_id and self.lookup_list_id and self.lookup_list.kind != "category":
            raise ValidationError(
                {"parent": "Les valeurs de liste déroulante ne sont pas hiérarchiques."}
            )
        if self.parent_id and self.pk:
            ancestor = self.parent
            seen = set()
            while ancestor is not None:
                if ancestor.pk == self.pk or ancestor.pk in seen:
                    raise ValidationError(
                        {
                            "parent": (
                                "Une catégorie ne peut pas être sa propre parente ou descendante."
                            )
                        }
                    )
                seen.add(ancestor.pk)
                ancestor = ancestor.parent

    def __str__(self):
        return self.label


class SiteCommunicationSettings(models.Model):
    """Private site settings; contact reception belongs to its own domain."""

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="communication_settings"
    )
    smtp_enabled = models.BooleanField("Activer l’envoi SMTP", default=False)
    smtp_host = models.CharField(
        "Serveur SMTP",
        max_length=253,
        blank=True,
        validators=[
            RegexValidator(
                r"^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$",
                "Indiquez un nom de serveur, sans URL ni chemin.",
            )
        ],
    )
    smtp_port = models.PositiveIntegerField(
        "Port", default=587, validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    smtp_security = models.CharField(
        "Connexion sécurisée",
        max_length=8,
        default="starttls",
        choices=[
            ("starttls", "STARTTLS (habituellement 587)"),
            ("tls", "TLS (habituellement 465)"),
        ],
    )
    smtp_username = models.CharField("Identifiant SMTP", max_length=254, blank=True)
    smtp_from_email = models.EmailField("Adresse d’expédition", blank=True)
    smtp_from_name = models.CharField("Nom de l’expéditeur", max_length=100, blank=True)
    contact_recipient = models.EmailField("Destinataire des demandes", blank=True)
    contact_hourly_limit = models.PositiveIntegerField(
        "Demandes par heure et par site",
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(10000)],
    )
    contact_ip_hourly_limit = models.PositiveIntegerField(
        "Demandes par heure et par adresse IP",
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    contact_retention_days = models.PositiveIntegerField(
        "Conservation des demandes (jours)",
        default=90,
        validators=[MinValueValidator(30), MaxValueValidator(3650)],
    )
    contact_max_length = models.PositiveIntegerField(
        "Longueur maximale du message",
        default=10000,
        validators=[MinValueValidator(100), MaxValueValidator(50000)],
    )
    spam_terms = models.TextField("Termes indésirables", blank=True, max_length=10000)
    spam_scan_content = models.BooleanField("Analyser le message", default=True)
    spam_scan_metadata = models.BooleanField("Analyser le titre et les coordonnées", default=True)
    contact_addressbook_mode = models.CharField(
        "Liaison au carnet d’adresses",
        max_length=16,
        default="manual",
        choices=[
            ("manual", "Sur validation manuelle"),
            ("create", "Proposer l’ajout des nouvelles personnes"),
            ("sync", "Proposer l’ajout et la mise à jour des personnes"),
        ],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuration des communications du site"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    contact_retention_days__gte=30, contact_retention_days__lte=3650
                ),
                name="site_contact_retention_range",
            ),
            models.CheckConstraint(
                condition=models.Q(smtp_port__gte=1, smtp_port__lte=65535),
                name="site_smtp_port_range",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_hourly_limit__gte=1, contact_hourly_limit__lte=10000),
                name="site_contact_hourly_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    contact_ip_hourly_limit__gte=1, contact_ip_hourly_limit__lte=100
                ),
                name="site_contact_ip_hourly_range",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_ip_hourly_limit__lte=models.F("contact_hourly_limit")),
                name="site_contact_ip_within_total",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_max_length__gte=100, contact_max_length__lte=50000),
                name="site_contact_length_range",
            ),
            models.CheckConstraint(
                condition=models.Q(smtp_security__in=["starttls", "tls"]),
                name="site_smtp_security_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_addressbook_mode__in=["manual", "create", "sync"]),
                name="site_contact_addressbook_mode_valid",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        self.smtp_host = self.smtp_host.strip().lower()
        for field in ("smtp_username", "smtp_from_name"):
            if any(character in getattr(self, field) for character in ("\r", "\n", "\x00")):
                errors[field] = "Les sauts de ligne et caractères de contrôle sont interdits."
        if self.smtp_enabled:
            for field in ("smtp_host", "smtp_from_email"):
                if not getattr(self, field):
                    errors[field] = "Ce champ est requis pour activer SMTP."
            if self.smtp_host not in settings.SITE_SMTP_ALLOWED_HOSTS:
                errors["smtp_host"] = (
                    "Ce serveur doit être autorisé par l’administrateur technique."
                )
        if self.contact_ip_hourly_limit > self.contact_hourly_limit:
            errors["contact_ip_hourly_limit"] = (
                "Le quota par IP ne peut pas dépasser le quota du site."
            )
        from .communications import normalize_spam_terms

        try:
            self.spam_terms = "\n".join(normalize_spam_terms(self.spam_terms))
        except ValidationError as error:
            errors["spam_terms"] = error.messages
        if self.spam_terms and not (self.spam_scan_content or self.spam_scan_metadata):
            errors["spam_terms"] = "Sélectionnez au moins une zone à analyser."
        if errors:
            raise ValidationError(errors)
