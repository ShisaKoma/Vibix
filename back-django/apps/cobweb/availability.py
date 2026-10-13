from apps.configuration.models import LookupEntry


def active_function_codes(site, *, group=False):
    """A site's active root categories determine its Cobweb function shortcuts."""
    field_key = "cobweb_group.category" if group else "cobweb_item.category"
    return set(
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="cobweb",
            lookup_list__kind="category",
            lookup_list__field_key__in=("", field_key),
            lookup_list__is_active=True,
            parent__isnull=True,
            is_active=True,
        ).values_list("value", flat=True)
    )
