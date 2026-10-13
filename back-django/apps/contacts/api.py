import json
import uuid
from functools import wraps

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from apps.tenancy.models import Site

from .definitions import schema_for
from .forms import SubmissionForm
from .models import QUESTION_KEYS, ContactRequest
from .reception import FormChanged, ReceiptConflict, ReceptionClosed, receive
from .security import (
    MAX_BODY_BYTES,
    authenticate_client,
    challenge_receipt,
    consume_quota,
    digest,
    issue_challenge,
    policy_for,
)


def private_response(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except Http404:
            response = error("not_found", 404)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    return wrapped


def public_site(site_id):
    site = (
        Site.objects.filter(
            pk=site_id, is_active=True, public_access_enabled=True, contacts_enabled=True
        )
        .exclude(pk=1)
        .first()
    )
    if not site:
        raise Http404
    return site


def error(code, status, **values):
    return JsonResponse({"error": code, **values}, status=status)


def quota_response(delay):
    response = error("quota_exceeded", 429)
    response["Retry-After"] = str(delay)
    return response


@private_response
def challenge(request, site_id):
    if request.method != "GET":
        response = error("method_not_allowed", 405)
        response["Allow"] = "GET"
        return response
    site = public_site(site_id)
    allowed, delay = consume_quota(request, site, challenge=True)
    if not allowed:
        return quota_response(delay)
    return JsonResponse(issue_challenge(request, site))


def submit_json(request, site, client):
    try:
        length = int(request.headers.get("Content-Length", "0"))
    except ValueError:
        return error("invalid_request", 400)
    if length < 0 or length > MAX_BODY_BYTES:
        return error("payload_too_large", 413)
    if request.content_type != "application/json":
        return error("unsupported_media_type", 415)
    try:
        raw = request.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            return error("payload_too_large", 413)

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        data = json.loads(raw, object_pairs_hook=unique_object)
    except RequestDataTooBig:
        return error("payload_too_large", 413)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return error("invalid_json", 400)
    if (
        not isinstance(data, dict)
        or set(data) - (set(SubmissionForm.base_fields) | set(QUESTION_KEYS))
        or any(not isinstance(value, str) for value in data.values())
    ):
        return error("invalid_fields", 400)
    if client:
        try:
            key = str(uuid.UUID(request.headers.get("Idempotency-Key", "")))
        except ValueError:
            return error("idempotency_key_required", 400)
        receipt = digest(f"api:{client.pk}:{key}")
    else:
        receipt = challenge_receipt(request, site, data.get("challenge", ""))
        if not receipt:
            return error("invalid_challenge", 400)
    schema = schema_for(site, data.get("form_type", ""), receipt=receipt)
    existing = ContactRequest.objects.filter(site=site, receipt_hash=receipt).first()
    if (
        existing
        and existing.form_snapshot
        and (
            data.get("form_type", schema["code"]) != schema["code"]
            or data.get("form_version", schema["version"]) != schema["version"]
        )
    ):
        return error("idempotency_conflict", 409)
    form = SubmissionForm(data, max_length=policy_for(site).contact_max_length, schema=schema)
    if not form.is_valid():
        return error("validation_failed", 400, fields=form.errors.get_json_data(escape_html=True))
    if set(data) - set(form.fields):
        return error("invalid_fields", 400)
    if not client and not challenge_receipt(
        request,
        site,
        data.get("challenge", ""),
        form_type=form.cleaned_data["form_type"],
        form_version=form.cleaned_data["form_version"],
    ):
        return error("invalid_challenge", 400)
    try:
        receive(site, form.cleaned_data, receipt, client=client)
    except FormChanged:
        return error("form_changed", 409)
    except ReceiptConflict:
        return error("idempotency_conflict", 409)
    except ReceptionClosed:
        return error("not_found", 404)
    except ValidationError:
        return error("validation_failed", 400)
    return JsonResponse({"status": "accepted"}, status=202)


@private_response
@csrf_exempt
def submit(request, site_id):
    if request.method != "POST":
        response = error("method_not_allowed", 405)
        response["Allow"] = "POST"
        return response
    try:
        site = public_site(site_id)
    except Http404:
        return error("not_found", 404)
    allowed, delay = consume_quota(request, site)
    if not allowed:
        return quota_response(delay)
    if request.headers.get("Authorization"):
        client = authenticate_client(request, site)
        if not client:
            response = error("authentication_required", 401)
            response["WWW-Authenticate"] = "Bearer"
            return response
        return submit_json(request, site, client)
    # Exemption applies only to scoped Bearer clients. Browser submissions always
    # pass Django's CSRF check, including Origin/Referer validation.
    return csrf_protect(submit_json)(request, site, None)
