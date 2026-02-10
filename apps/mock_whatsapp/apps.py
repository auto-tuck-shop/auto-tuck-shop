from django.apps import AppConfig


class MockWhatsappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mock_whatsapp"
    verbose_name = "Mock WhatsApp"
