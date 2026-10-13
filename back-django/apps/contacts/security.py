import ipaddress
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.configuration.models import SiteCommunicationSettings
from apps.tenancy.models import Site

from .definitions import available_forms, schema_for
from .models import APIClient, QuotaBucket

CHALLENGE_TTL = 1800
MIN_FORM_SECONDS = 2
MAX_BODY_BYTES = 256 * 1024


def digest(value):
    return salted_hmac("contacts", value, algorithm="sha256").hexdigest()


def remote_identity(request):
    remote = request.META.get("REMOTE_ADDR", "unknown")
    # The proxy must overwrite this dedicated header and be the only ingress.
    # X-Forwarded-For is deliberately ignored, including for trusted proxies.
    if remote in settings.CONTACTS_TRUSTED_PROXY_IPS:
        try:
            remote = str(ipaddress.ip_address(request.headers.get("X-Contacts-Client-IP", "")))
        except ValueError:
            pass
    return digest(remote)


def policy_for(site):
    return SiteCommunicationSettings.objects.filter(site=site).first() or SiteCommunicationSettings(
        site=site
    )


def consume_quota(request, site, *, challenge=False):
    """Serialize both buckets on the site; commit consumption even for rejected payloads."""
    now = timezone.now()
    hour = now.replace(minute=0, second=0, microsecond=0)
    scope = "challenge" if challenge else "submit"
    with transaction.atomic():
        locked = Site.objects.select_for_update(no_key=True).get(pk=site.pk)
        policy = policy_for(locked)
        limits = [
            ("site", policy.contact_hourly_limit * (10 if challenge else 1)),
            (remote_identity(request), policy.contact_ip_hourly_limit * (10 if challenge else 1)),
        ]
        for identity, limit in limits:
            bucket, _ = QuotaBucket.objects.get_or_create(
                site=locked, scope=scope, identity=identity, hour=hour
            )
            if bucket.count >= limit:
                return False, max(1, int((hour + timedelta(hours=1) - now).total_seconds()))
            bucket.count += 1
            bucket.save(update_fields=["count"])
    return True, 0


def issue_challenge(request, site, schema=None):
    schema = schema or schema_for(site, request.GET.get("type", ""))
    csrf = get_token(request)
    payload = {
        "site": site.pk,
        "form_type": schema["code"],
        "form_version": schema["version"],
        "ip": remote_identity(request),
        "csrf": digest(request.META["CSRF_COOKIE"]),
        "nonce": secrets.token_urlsafe(24),
        "issued": time.time(),
    }
    return {
        "challenge": signing.dumps(payload, salt="contacts.challenge", compress=False),
        "csrf_token": csrf,
        "expires_in": CHALLENGE_TTL,
        "minimum_delay": MIN_FORM_SECONDS,
        "max_message_length": policy_for(site).contact_max_length,
        "retention_days": policy_for(site).contact_retention_days,
        "form": schema,
        "forms": [{"code": form["code"], "label": form["label"]} for form in available_forms(site)],
    }


def challenge_receipt(request, site, token, *, form_type=None, form_version=None):
    try:
        payload = signing.loads(token, salt="contacts.challenge", max_age=CHALLENGE_TTL)
        if (
            payload["site"] != site.pk
            or (form_type is not None and payload.get("form_type") != form_type)
            or (form_version is not None and payload.get("form_version") != form_version)
            or payload["ip"] != remote_identity(request)
            or payload["csrf"] != digest(request.META.get("CSRF_COOKIE", ""))
            or not MIN_FORM_SECONDS <= time.time() - payload["issued"] <= CHALLENGE_TTL
        ):
            return None
        return digest(f"web:{payload['nonce']}")
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None


def authenticate_client(request, site):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 150:
        return None
    token = authorization.removeprefix("Bearer ")
    return APIClient.objects.filter(
        site=site,
        token_hash=digest(token),
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
