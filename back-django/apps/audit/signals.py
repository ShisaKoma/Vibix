from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditEvent


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    AuditEvent.objects.create(actor=user, action="auth.login")


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    AuditEvent.objects.create(actor=user, action="auth.logout")


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    # Never store credentials, submitted usernames, cookies or IPs in this journal.
    AuditEvent.objects.create(action="auth.login_failed")
