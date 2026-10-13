"""SMTP adapter and bounded literal spam matching for the Contacts module."""

import os
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail.backends.smtp import EmailBackend


def normalized_text(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(char for char in value if not unicodedata.combining(char)).split())


def normalize_spam_terms(value):
    if len(value) > 10000:
        raise ValidationError("La liste de termes dépasse 10 000 caractères.")
    terms = list(
        dict.fromkeys(normalized_text(line) for line in value.splitlines() if line.strip())
    )
    if len(terms) > 200 or any(not term or len(term) > 100 for term in terms):
        raise ValidationError("Indiquez au maximum 200 termes de 1 à 100 caractères, un par ligne.")
    return terms


def is_contact_spam(policy, *, message="", title="", name="", email="", phone="", organization=""):
    """Inspect only defined contact fields, never arbitrary HTTP headers or regexes."""
    values = []
    if policy.spam_scan_content:
        values.append(message)
    if policy.spam_scan_metadata:
        values.extend((title, name, email, phone, organization))
    # Keep matching bounded even when called outside a validated API.
    if any(not isinstance(value, str) or len(value) > 50000 for value in values):
        raise ValidationError("Le contenu à analyser dépasse les limites autorisées.")
    texts = [normalized_text(value) for value in values]
    return any(term in text for term in normalize_spam_terms(policy.spam_terms) for text in texts)


def smtp_password_configured(site_id):
    return bool(os.environ.get(f"SITE_SMTP_PASSWORD_{site_id}", ""))


def smtp_connection(configuration):
    """Return an unopened TLS connection; never fall back to global SMTP credentials."""
    configuration.full_clean()
    if not configuration.smtp_enabled:
        raise ValidationError("L’envoi SMTP est désactivé pour ce site.")
    password = os.environ.get(f"SITE_SMTP_PASSWORD_{configuration.site_id}", "")
    if configuration.smtp_username and not password:
        raise ValidationError("Le secret SMTP de ce site doit être configuré sur le serveur.")
    if configuration.smtp_host not in settings.SITE_SMTP_ALLOWED_HOSTS:
        raise ValidationError("Le serveur SMTP n’est pas autorisé.")
    return EmailBackend(
        host=configuration.smtp_host,
        port=configuration.smtp_port,
        username=configuration.smtp_username,
        password=password if configuration.smtp_username else "",
        use_tls=configuration.smtp_security == "starttls",
        use_ssl=configuration.smtp_security == "tls",
        timeout=10,
        fail_silently=False,
    )
