from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse


class ContentRecord(models.Model):
    """A shared registry with real foreign keys to the implemented source modules."""

    site = models.ForeignKey("tenancy.Site", on_delete=models.PROTECT)
    item = models.OneToOneField(
        "cobweb.CobwebItem",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_record",
    )
    group = models.OneToOneField(
        "cobweb.CobwebGroup",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_record",
    )
    article = models.OneToOneField(
        "articles.Article",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_record",
    )

    page = models.OneToOneField(
        "pages.Page",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_record",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        item__isnull=False,
                        group__isnull=True,
                        article__isnull=True,
                        page__isnull=True,
                    )
                    | models.Q(
                        item__isnull=True,
                        group__isnull=False,
                        article__isnull=True,
                        page__isnull=True,
                    )
                    | models.Q(
                        item__isnull=True,
                        group__isnull=True,
                        article__isnull=False,
                        page__isnull=True,
                    )
                    | models.Q(
                        item__isnull=True,
                        group__isnull=True,
                        article__isnull=True,
                        page__isnull=False,
                    )
                ),
                name="content_record_one_source",
            )
        ]

    @property
    def source(self):
        return (
            self.page
            if self.page_id
            else self.article
            if self.article_id
            else self.item
            if self.item_id
            else self.group
        )

    @property
    def permission(self):
        if self.page_id:
            return "pages.view_page"
        if self.article_id:
            return "articles.view_article"
        return "cobweb.view_cobwebitem" if self.item_id else "cobweb.view_cobwebgroup"

    @property
    def public_url(self):
        if self.page_id:
            return reverse(
                "pages:public-slug",
                args=[
                    self.site_id,
                    self.page.url_language,
                    self.page.slug,
                ],
            )
        if self.article_id:
            return reverse("articles:public-detail", args=[self.site_id, self.article_id])
        route = "public-item" if self.item_id else "public-group"
        return reverse(f"cobweb:{route}", args=[self.site_id, self.source.pk])

    @property
    def detail_url(self):
        route = (
            "pages:detail"
            if self.page_id
            else "articles:detail"
            if self.article_id
            else "cobweb:detail"
            if self.item_id
            else "cobweb:group-detail"
        )
        return reverse(route, args=[self.site_id, self.source.pk])

    @property
    def preview_url(self):
        route = (
            "pages:preview"
            if self.page_id
            else "articles:preview"
            if self.article_id
            else "cobweb:preview-item"
            if self.item_id
            else "cobweb:preview-group"
        )
        return reverse(route, args=[self.site_id, self.source.pk])

    @property
    def is_available(self):
        return getattr(self.source, "deleted_at", None) is None

    def clean(self):
        super().clean()
        if (
            sum(
                bool(value)
                for value in (self.item_id, self.group_id, self.article_id, self.page_id)
            )
            != 1
        ):
            raise ValidationError("Une référence doit désigner exactement un contenu.")
        if self.source.site_id != self.site_id:
            raise ValidationError("Le contenu doit appartenir au site de la référence.")

    def __str__(self):
        if self.page_id:
            return f"Pages · {self.page.title}"
        if self.article_id:
            return f"Articles · {self.article.title}"
        kind = "Entrée" if self.item_id else "Groupe"
        return f"Cobweb · {kind} · {self.source.title}"


class ContentLink(models.Model):
    source = models.ForeignKey(
        ContentRecord, on_delete=models.CASCADE, related_name="outgoing_links"
    )
    target = models.ForeignKey(
        ContentRecord, on_delete=models.CASCADE, related_name="incoming_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "target"], name="unique_content_link"),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")), name="content_no_self_link"
            ),
        ]

    def clean(self):
        super().clean()
        if self.source_id and self.target_id:
            if self.source_id == self.target_id or self.source.site_id != self.target.site_id:
                raise ValidationError("La liaison doit viser un autre contenu du même site.")
