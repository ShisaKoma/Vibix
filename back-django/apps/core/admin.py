from django.contrib import admin

from apps.audit.models import AuditEvent


def technical_admin_permission(request):
    user = request.user
    return user.is_active and user.is_staff and user.is_superuser


# Django Admin has global scope; site operators will use dedicated scoped views.
admin.site.has_permission = technical_admin_permission
admin.site.site_header = "ViBix — administration technique"
admin.site.site_title = "ViBix"


class AuditAdminMixin:
    def record_audit(self, request, obj, action):
        AuditEvent.objects.create(
            actor=request.user,
            site_id=getattr(obj, "site_id", None),
            action=action,
            entity_type=obj._meta.label_lower,
            entity_id=str(obj.pk),
        )

    def log_addition(self, request, obj, message):
        self.record_audit(request, obj, "admin.create")
        return super().log_addition(request, obj, message)

    def log_change(self, request, obj, message):
        self.record_audit(request, obj, "admin.update")
        return super().log_change(request, obj, message)

    def log_deletions(self, request, queryset):
        for obj in queryset:
            self.record_audit(request, obj, "admin.delete")
        return super().log_deletions(request, queryset)
