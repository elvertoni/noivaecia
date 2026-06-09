from django.apps import AppConfig


class MovementsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'movements'

    def ready(self):
        from . import signals  # noqa: F401
