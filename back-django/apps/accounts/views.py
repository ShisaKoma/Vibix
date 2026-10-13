from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.audit.models import AuditEvent
from apps.tenancy.permissions import has_site_permission

from .forms import FirstRunForm
from .models import UserProfile


@login_required
@require_http_methods(["GET", "POST"])
def first_run(request):
    existing = UserProfile.objects.filter(user=request.user).first()
    if existing and existing.first_run_completed_at:
        return redirect("dashboard")

    form = FirstRunForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        site = form.cleaned_data["preferred_site"]
        with transaction.atomic():
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.preferred_site = site
            profile.first_run_completed_at = timezone.now()
            profile.save(update_fields=["preferred_site", "first_run_completed_at"])
            AuditEvent.objects.create(
                actor=request.user,
                site=site,
                action="auth.first_run.completed",
                entity_type="accounts.userprofile",
                entity_id=str(profile.pk),
            )
        messages.success(request, "Votre espace ViBix est prêt.")
        return redirect("dashboard")

    sites = list(form.fields["preferred_site"].queryset)
    site_cards = [
        {
            "site": site,
            "can_manage_settings": has_site_permission(
                request.user, site, "tenancy.manage_site_settings"
            ),
        }
        for site in sites
    ]
    return render(
        request,
        "accounts/first_run.html",
        {"form": form, "site_cards": site_cards},
    )
