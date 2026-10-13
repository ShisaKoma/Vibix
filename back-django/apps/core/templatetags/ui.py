from django import template

from apps.cobweb.availability import active_function_codes
from apps.core.navigation import COBWEB_FUNCTIONS, UI_FUNCTIONS

register = template.Library()


@register.inclusion_tag("workspace/icon.html")
def ui_icon(key):
    return {"icon": UI_FUNCTIONS[key]["icon"]}


@register.inclusion_tag("workspace/module_label.html")
def module_label(key):
    return UI_FUNCTIONS[key]


@register.simple_tag
def module_name(key):
    return UI_FUNCTIONS[key]["label"]


@register.inclusion_tag("workspace/cobweb_functions.html", takes_context=True)
def cobweb_functions(context, variant="sidebar"):
    enabled = active_function_codes(context["site"])
    return {
        "site": context["site"],
        "variant": variant,
        "active_function": context.get("cobweb_function"),
        "functions": [
            {"code": code, **UI_FUNCTIONS[key]}
            for code, key in COBWEB_FUNCTIONS.items()
            if code in enabled
        ],
    }
