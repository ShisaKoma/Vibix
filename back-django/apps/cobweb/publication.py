from django.db.models import Q

from apps.configuration.models import LookupEntry
from apps.tenancy.constants import OWNER_SITE_ID


def category_available(category):
    if not category.lookup_list.is_active:
        return False
    seen = set()
    while category is not None:
        if category.pk in seen or not category.is_active:
            return False
        seen.add(category.pk)
        category = category.parent
    return True


def publicly_visible(content):
    return (
        content.site_id != OWNER_SITE_ID
        and content.site.is_active
        and content.site.public_access_enabled
        and content.site.module_enabled(content._meta.app_label)
        and content.is_in_publication_window
        and category_available(content.category)
        and (not getattr(content, "group_id", None) or category_available(content.group))
    )


def available_category_ids(entries):
    """Resolve active ancestry once per tree, without querying for each content."""
    nodes = {entry.pk: entry for entry in entries}
    availability = {}
    for pk in nodes:
        cursor, path = pk, set()
        while cursor is not None and cursor not in availability:
            entry = nodes.get(cursor)
            if (
                entry is None
                or cursor in path
                or not entry.is_active
                or not entry.lookup_list.is_active
            ):
                available = False
                break
            path.add(cursor)
            cursor = entry.parent_id
        else:
            available = cursor is None or availability[cursor]
        for ancestor in path:
            availability[ancestor] = available
        availability[pk] = available
    return {pk for pk, available in availability.items() if available}


def descendant_ids(category):
    pairs = list(
        LookupEntry.objects.filter(lookup_list_id=category.lookup_list_id).values_list(
            "pk", "parent_id"
        )
    )
    selected = {category.pk}
    frontier = {category.pk}
    while frontier:
        frontier = {pk for pk, parent in pairs if parent in frontier and pk not in selected}
        selected.update(frontier)
    return selected


def apply_search(queryset, data):
    if data["q"]:
        condition = Q(title__icontains=data["q"]) | Q(content__icontains=data["q"])
        if hasattr(queryset.model, "reference"):
            condition |= Q(reference__icontains=data["q"]) | Q(subtitle__icontains=data["q"])
        if hasattr(queryset.model, "slug"):
            condition |= Q(slug__icontains=data["q"])
        queryset = queryset.filter(condition)
    if data["category"]:
        ids = (
            descendant_ids(data["category"])
            if data["include_descendants"]
            else {data["category"].pk}
        )
        queryset = queryset.filter(
            Q(category_id__in=ids) | Q(additional_categories__pk__in=ids)
        ).distinct()
    for key, field in (("active", "is_active"), ("online", "is_online")):
        if data[key]:
            queryset = queryset.filter(**{field: data[key] == "yes"})
    if data["created_from"]:
        queryset = queryset.filter(created_at__date__gte=data["created_from"])
    if data["created_until"]:
        queryset = queryset.filter(created_at__date__lte=data["created_until"])
    return queryset.order_by(data["order"] or "position", "pk")
