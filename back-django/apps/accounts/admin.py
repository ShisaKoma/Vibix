from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.core.admin import AuditAdminMixin

from .models import User


@admin.register(User)
class AccountAdmin(AuditAdminMixin, UserAdmin):
    pass
