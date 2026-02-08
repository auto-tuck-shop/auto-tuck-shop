from django.urls import path

from apps.testing.views import MockMediaView, OutboxView

urlpatterns = [
    path("outbox/", OutboxView.as_view(), name="test-outbox"),
    path("mock-media/", MockMediaView.as_view(), name="test-mock-media"),
]
