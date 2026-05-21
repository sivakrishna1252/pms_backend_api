from django.apps import AppConfig


class PmsApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pms_api"

    def ready(self):
        import pms_api.schema  # noqa: F401
