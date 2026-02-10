"""
Mock WhatsApp web UI for staging.

Provides a WhatsApp-like chat interface that sends messages through the real
webhook handler pipeline. Requires Django admin (staff) login.
"""

import json
import logging
import uuid

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.whatsapp.views import WhatsAppWebhookView
from services.whatsapp.mock_client import MockWhatsAppClient

logger = logging.getLogger(__name__)


@method_decorator(staff_member_required, name="dispatch")
class MockChatView(View):
    """Serve the mock WhatsApp chat UI."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return TemplateResponse(request, "mock_whatsapp/chat.html")


@method_decorator(staff_member_required, name="dispatch")
@method_decorator(csrf_exempt, name="dispatch")
class MockSendView(View):
    """
    Accept a message from the mock UI and feed it into the webhook handler.

    POST JSON: {"phone": "+27821234567", "text": "hello"}
    or:        {"phone": "+27821234567", "type": "audio"}
    or:        {"phone": "+27821234567", "type": "button", "button_id": "confirm_123"}
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse("Invalid JSON", status=400)

        phone = data.get("phone", "").strip()
        if not phone:
            return HttpResponse("phone is required", status=400)

        # Strip + for Meta-style phone number
        from_number = phone.lstrip("+")
        msg_type = data.get("type", "text")
        message_id = f"wamid.mock_{uuid.uuid4().hex[:16]}"

        # Build a Meta-shaped message dict
        if msg_type == "text":
            text = data.get("text", "").strip()
            if not text:
                return HttpResponse("text is required for text messages", status=400)
            message = {
                "id": message_id,
                "from": from_number,
                "type": "text",
                "text": {"body": text},
            }
        elif msg_type == "audio":
            media_id = f"mock_media_{uuid.uuid4().hex[:12]}"
            # Pre-load empty audio so download_media doesn't fail
            MockWhatsAppClient.media_downloads[media_id] = (b"", "audio/ogg")
            message = {
                "id": message_id,
                "from": from_number,
                "type": "audio",
                "audio": {"id": media_id},
            }
        elif msg_type == "button":
            button_id = data.get("button_id", "")
            original_message_id = data.get("original_message_id", "")
            if not button_id:
                return HttpResponse("button_id is required for button messages", status=400)
            message = {
                "id": message_id,
                "from": from_number,
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": button_id},
                },
                "context": {"id": original_message_id},
            }
        else:
            return HttpResponse(f"Unknown message type: {msg_type}", status=400)

        # Feed directly into the webhook handler
        view = WhatsAppWebhookView()
        view._handle_message(message)

        return JsonResponse({"ok": True, "message_id": message_id})


@method_decorator(staff_member_required, name="dispatch")
class MockOutboxView(View):
    """
    Read the mock outbox for a phone number.

    GET /mock-whatsapp/outbox/?phone=+27821234567&since=5
    Returns messages and buttons from MockWhatsAppClient.

    Query params:
        phone  — phone number to filter by (required)
        since  — only return messages after this index (for polling)
        clear  — if "1", clear outbox for this phone after reading
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        phone = request.GET.get("phone", "")
        since_messages = int(request.GET.get("since_messages", 0))
        since_buttons = int(request.GET.get("since_buttons", 0))

        if not phone:
            return JsonResponse({"error": "phone is required"}, status=400)

        # Match with or without '+' prefix
        variants = {phone, phone.lstrip("+"), "+" + phone.lstrip("+")}

        messages = [m for m in MockWhatsAppClient.sent_messages if m["to"] in variants]
        buttons = [b for b in MockWhatsAppClient.sent_buttons if b["to"] in variants]

        # Return only new messages since the given index
        new_messages = messages[since_messages:]
        new_buttons = buttons[since_buttons:]

        return JsonResponse({
            "messages": new_messages,
            "buttons": new_buttons,
            "total_messages": len(messages),
            "total_buttons": len(buttons),
        })

    def delete(self, request: HttpRequest) -> HttpResponse:
        """Clear the outbox."""
        MockWhatsAppClient.reset()
        return HttpResponse("OK")
