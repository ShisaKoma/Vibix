from django.shortcuts import redirect

from .models import UserProfile


class FirstRunMiddleware:
    """Require the first-run setup before showing authenticated application pages."""

    exempt_paths = {"/onboarding/", "/accounts/logout/", "/docs/openapi", "/docs/openapi/"}
    exempt_prefixes = ("/static/", "/health/", "/api/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if (
            request.user.is_authenticated
            and path not in self.exempt_paths
            and not path.startswith(self.exempt_prefixes)
        ):
            completed = UserProfile.objects.filter(
                user_id=request.user.pk, first_run_completed_at__isnull=False
            ).exists()
            if not completed:
                return redirect("first-run")
        return self.get_response(request)
