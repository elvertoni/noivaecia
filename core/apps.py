from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Register core so optional integrations can autodiscover its modules."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
