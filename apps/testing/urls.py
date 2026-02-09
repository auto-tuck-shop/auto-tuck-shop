from django.urls import path

from apps.testing.views import MockMediaView, OutboxView, SampleAudioView

urlpatterns = [
    path("outbox/", OutboxView.as_view(), name="test-outbox"),
    path("mock-media/", MockMediaView.as_view(), name="test-mock-media"),
    path("r2-sample-audio/", SampleAudioView.as_view(), name="test-r2-sample-audio"),
]
