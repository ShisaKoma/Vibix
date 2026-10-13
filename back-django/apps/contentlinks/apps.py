from django.apps import AppConfig


class ContentlinksConfig(AppConfig):
    name = "apps.contentlinks"
    verbose_name = "Liaisons de contenus"

    def ready(self):
        from . import signals  # noqa: F401
