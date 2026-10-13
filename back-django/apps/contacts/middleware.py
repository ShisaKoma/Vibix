from django.http import JsonResponse

from .security import MAX_BODY_BYTES


class ContactsBodyLimitMiddleware:
    """Reject oversized declared contact bodies before Django parses CSRF forms."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and (
            "/contacts/" in request.path_info or request.path_info.endswith("/contact/")
        ):
            try:
                length = int(request.META.get("CONTENT_LENGTH", "0") or "0")
            except ValueError:
                return JsonResponse({"error": "invalid_request"}, status=400)
            if length < 0 or length > MAX_BODY_BYTES:
                response = JsonResponse({"error": "payload_too_large"}, status=413)
                response["Cache-Control"] = "no-store"
                return response
        return self.get_response(request)
