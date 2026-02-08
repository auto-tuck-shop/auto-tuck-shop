"""
Staging-only test endpoints for reading the mock WhatsApp outbox
and pre-loading mock media for audio tests.
"""

import base64
import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from services.whatsapp.mock_client import MockWhatsAppClient

logger = logging.getLogger(__name__)


def _check_api_key(request: HttpRequest) -> HttpResponse | None:
    """Return an error response if the API key is missing/wrong, else None."""
    expected = getattr(settings, "TEST_API_KEY", None)
    if not expected:
        return HttpResponse("Test API not configured", status=503)
    provided = request.headers.get("X-Test-Api-Key", "")
    if provided != expected:
        return HttpResponse("Forbidden", status=403)
    return None


@method_decorator(csrf_exempt, name="dispatch")
class OutboxView(View):
    """GET /test/outbox/?phone=+27821234567 — read mock outbound messages."""

    def get(self, request: HttpRequest) -> JsonResponse:
        err = _check_api_key(request)
        if err:
            return err

        phone = request.GET.get("phone", "")

        if phone:
            # Normalize: match with or without '+' prefix
            variants = {phone, phone.lstrip("+"), "+" + phone.lstrip("+")}
            messages = [m for m in MockWhatsAppClient.sent_messages if m["to"] in variants]
            buttons = [b for b in MockWhatsAppClient.sent_buttons if b["to"] in variants]
        else:
            messages = list(MockWhatsAppClient.sent_messages)
            buttons = list(MockWhatsAppClient.sent_buttons)

        return JsonResponse({"messages": messages, "buttons": buttons})

    def delete(self, request: HttpRequest) -> HttpResponse:
        """DELETE /test/outbox/ — reset all mock state."""
        err = _check_api_key(request)
        if err:
            return err
        MockWhatsAppClient.reset()
        return HttpResponse("OK", status=200)


@method_decorator(csrf_exempt, name="dispatch")
class MockMediaView(View):
    """POST /test/mock-media/ — pre-load audio for mock WhatsApp downloads."""

    def post(self, request: HttpRequest) -> HttpResponse:
        err = _check_api_key(request)
        if err:
            return err

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse("Invalid JSON", status=400)

        media_id = data.get("media_id")
        audio_b64 = data.get("audio_base64")
        mime_type = data.get("mime_type", "audio/ogg")

        if not media_id or not audio_b64:
            return HttpResponse("media_id and audio_base64 required", status=400)

        audio_bytes = base64.b64decode(audio_b64)
        MockWhatsAppClient.media_downloads[media_id] = (audio_bytes, mime_type)

        logger.info(f"[TEST] Loaded mock media {media_id}: {len(audio_bytes)} bytes")
        return HttpResponse("OK", status=200)
