from django.contrib import admin

from apps.addressbook.seeds import seed_addressbook_references
from apps.cobweb.seeds import seed_cobweb_references
from apps.configuration.seeds import seed_client_references, seed_editorial_references
from apps.contacts.seeds import seed_contact_forms
from apps.core.admin import AuditAdminMixin

from .constants import OWNER_SITE_ID
from .forms import SiteSettingsForm
from .models import Role, Site, SiteMembership


class SiteAdminForm(SiteSettingsForm):
    class Meta(SiteSettingsForm.Meta):
        fields = "__all__"


@admin.register(Site)
class SiteAdmin(AuditAdminMixin, admin.ModelAdmin):
    form = SiteAdminForm
    list_display = ["code", "name", "is_active"]
    search_fields = ["code", "name"]
    list_filter = ["is_active"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            seed_editorial_references(obj, actor=request.user)
            seed_client_references(obj, actor=request.user)
            seed_cobweb_references(obj, actor=request.user)
            seed_addressbook_references(obj, actor=request.user)
            seed_contact_forms(obj, actor=request.user)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.pk == OWNER_SITE_ID:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Role)
class RoleAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ["code", "name"]
    filter_horizontal = ["permissions"]


@admin.register(SiteMembership)
class MembershipAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ["user", "site", "role", "is_active"]
    list_filter = ["site", "role", "is_active"]
    autocomplete_fields = ["user", "site"]
