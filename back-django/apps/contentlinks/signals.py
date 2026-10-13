from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.articles.models import Article
from apps.cobweb.models import CobwebGroup, CobwebItem
from apps.pages.models import Page

from .models import ContentRecord


@receiver(post_save, sender=CobwebItem)
@receiver(post_save, sender=CobwebGroup)
@receiver(post_save, sender=Article)
@receiver(post_save, sender=Page)
def register_content(sender, instance, raw=False, **kwargs):
    if raw:
        return
    field = (
        "page"
        if sender is Page
        else "article"
        if sender is Article
        else "item"
        if sender is CobwebItem
        else "group"
    )
    ContentRecord.objects.update_or_create(
        **{field: instance}, defaults={"site_id": instance.site_id}
    )
