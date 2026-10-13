from django.contrib import admin

from apps.core.admin import AuditAdminMixin

from .models import LookupEntry, LookupList


class EntryInline(admin.TabularInline):
    model = LookupEntry
    extra = 0


@admin.register(LookupList)
class LookupListAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ["site", "module", "kind", "code", "name", "is_active"]
    list_filter = ["site", "module", "kind", "is_active"]
    search_fields = ["code", "name"]
    inlines = [EntryInline]


@admin.register(LookupEntry)
class LookupEntryAdmin(AuditAdminMixin, admin.ModelAdmin):
    list_display = ["lookup_list", "value", "label", "position", "is_active"]
    list_filter = ["lookup_list__site", "lookup_list__module", "lookup_list__kind", "is_active"]
    search_fields = ["value", "label"]
