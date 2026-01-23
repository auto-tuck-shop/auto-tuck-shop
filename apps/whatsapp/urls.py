from django.urls import path

from apps.whatsapp.views import WhatsAppWebhookView

urlpatterns = [
    path("whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
]
