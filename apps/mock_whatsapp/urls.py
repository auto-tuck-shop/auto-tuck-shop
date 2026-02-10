from django.urls import path

from apps.mock_whatsapp.views import MockChatView, MockSendView, MockOutboxView

urlpatterns = [
    path("", MockChatView.as_view(), name="mock-whatsapp-chat"),
    path("send/", MockSendView.as_view(), name="mock-whatsapp-send"),
    path("outbox/", MockOutboxView.as_view(), name="mock-whatsapp-outbox"),
]
