from django.apps import AppConfig


class EfeConfig(AppConfig):
    """efeTrack extras: notification center, random pick, film series."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "efe"
    verbose_name = "efeTrack"

    def ready(self):
        """Connect the Celery signal handlers."""
        from efe import signals  # noqa: F401, PLC0415
