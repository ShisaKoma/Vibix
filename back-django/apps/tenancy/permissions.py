from .constants import OWNER_SITE_ID
from .models import Site, SiteMembership


def has_site_permission(user, site, permission):
    """Always query current grants; global Django groups never grant site access."""
    if not user.is_authenticated or not user.is_active:
        return False
    site_id = site.pk if isinstance(site, Site) else site
    current_site = Site.objects.filter(pk=site_id, is_active=True).first()
    if current_site is None:
        return False
    app_label, separator, codename = permission.partition(".")
    if not separator or not app_label or not codename:
        return False
    if app_label in {
        "articles",
        "pages",
        "cobweb",
        "addressbook",
        "contacts",
        "planning",
        "products",
    } and not current_site.module_enabled(app_label):
        return False
    if user.is_superuser:
        return True
    if site_id == OWNER_SITE_ID:
        return False
    return SiteMembership.objects.filter(
        user=user,
        site_id=site_id,
        is_active=True,
        role__permissions__content_type__app_label=app_label,
        role__permissions__codename=codename,
    ).exists()
