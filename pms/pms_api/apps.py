from django.apps import AppConfig


class PmsApiConfig(AppConfig):
    name = 'pms_api'

    def ready(self):
        import pms_api.schema  # noqa: F401
