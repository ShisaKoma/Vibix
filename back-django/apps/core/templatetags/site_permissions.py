from django import template

from apps.tenancy.permissions import has_site_permission

register = template.Library()


@register.simple_tag(takes_context=True)
def site_permission(context, site, permission):
    request = context.get("request")
    return bool(request and site and has_site_permission(request.user, site, permission))
