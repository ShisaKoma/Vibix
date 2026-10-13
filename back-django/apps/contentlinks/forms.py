from django import forms

from apps.tenancy.permissions import has_site_permission

from .models import ContentLink, ContentRecord


def content_sections(records):
    """Classer uniquement les cibles autorisées ; la présentation ne sert pas de catégorie."""
    modules = {"page": [], "article": [], "item": [], "group": {}}
    for record in records:
        if record.page_id:
            modules["page"].append(record)
        elif record.article_id:
            modules["article"].append(record)
        elif record.item_id:
            modules["item"].append(record)
        else:
            category = record.group.category if record.group.category_id else None
            key = category.pk if category else None
            bucket = modules["group"].setdefault(
                key,
                {
                    "label": category.label if category else "Sans catégorie",
                    "reference": category.lookup_list.name if category else "",
                    "records": [],
                },
            )
            bucket["records"].append(record)
    sections = []
    for key, label in (
        ("page", "Pages"),
        ("article", "Articles"),
        ("item", "Entrées Cobweb"),
        ("group", "Groupes Cobweb"),
    ):
        if not modules[key]:
            continue
        if key == "group":
            groups = list(modules[key].values())
            labels = [group["label"].casefold() for group in groups]
            for group in groups:
                if labels.count(group["label"].casefold()) > 1 and group["reference"]:
                    group["label"] += " · " + group["reference"]
            groups.sort(key=lambda group: group["label"].casefold())
        else:
            groups = [{"label": "", "records": modules[key]}]
        for group in groups:
            group["records"].sort(key=lambda record: (record.source.title.casefold(), record.pk))
        sections.append({"label": label, "groups": groups})
    return sections


class ContentTargetsWidget(forms.CheckboxSelectMultiple):
    template_name = "contentlinks/targets_widget.html"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sections = []

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        options = {
            str(option["value"]): option
            for _, group, _ in context["widget"]["optgroups"]
            for option in group
        }
        context["widget"]["sections"] = [
            {
                "label": section["label"],
                "groups": [
                    {
                        "label": group["label"],
                        "options": [options[str(record.pk)] for record in group["records"]],
                        "selected": any(
                            options[str(record.pk)]["selected"] for record in group["records"]
                        ),
                    }
                    for group in section["groups"]
                ],
            }
            for section in self.sections
        ]
        return context


class ContentLinksForm(forms.Form):
    targets = forms.ModelMultipleChoiceField(
        label="Contenus associés",
        queryset=ContentRecord.objects.none(),
        required=False,
        widget=ContentTargetsWidget,
    )

    def __init__(self, *args, site, user, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        records = list(
            ContentRecord.objects.filter(site=site).select_related(
                "item", "group__category__lookup_list", "article", "page"
            )
        )
        permissions = {
            permission: has_site_permission(user, site, permission)
            for permission in {record.permission for record in records}
        }
        permitted = [
            record
            for record in records
            if (not source or record.pk != source.pk)
            and permissions[record.permission]
            and record.is_available
        ]
        field = self.fields["targets"]
        field.queryset = ContentRecord.objects.filter(pk__in=[record.pk for record in permitted])
        sections = content_sections(permitted)
        field.widget.sections = sections
        field.choices = [
            (record.pk, record.source.title)
            for section in sections
            for group in section["groups"]
            for record in group["records"]
        ]
        if source and not self.is_bound:
            self.initial["targets"] = source.outgoing_links.values_list("target_id", flat=True)

    def save(self, source):
        selected = self.cleaned_data["targets"]
        # Preserve links to contents this editor cannot view or select.
        source.outgoing_links.filter(target__in=self.fields["targets"].queryset).exclude(
            target__in=selected
        ).delete()
        for target in selected:
            ContentLink.objects.get_or_create(source=source, target=target)
