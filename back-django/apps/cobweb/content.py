import html

import nh3
from django.utils.html import escape
from django.utils.safestring import mark_safe


def clean_html(value):
    return nh3.clean(
        value,
        tags={
            "p",
            "br",
            "strong",
            "b",
            "em",
            "i",
            "u",
            "s",
            "ul",
            "ol",
            "li",
            "h2",
            "h3",
            "blockquote",
            "a",
        },
        attributes={"a": {"href", "title"}},
        url_schemes={"https", "http"},
        link_rel="noopener noreferrer",
    )


def rendered_content(value):
    # Sanitize on every render as well, including content written by imports or
    # older application versions. Plain legacy content retains its newlines.
    if "<" not in value:
        return mark_safe(str(escape(value)).replace("\n", "<br>"))
    return mark_safe(clean_html(value))


def searchable_text(value):
    return html.unescape(nh3.clean(value, tags=set()))
