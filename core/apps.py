from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        try:
            import core.signals  # noqa
        except Exception:
            pass
        try:
            from core.push_scheduler import start_push_digest_scheduler
            start_push_digest_scheduler()
        except Exception:
            pass
