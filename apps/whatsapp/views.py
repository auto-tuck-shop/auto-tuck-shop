import hashlib
import hmac
import json
import logging
from enum import Enum

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import UserProfile, WaitlistEntry
from apps.whatsapp.services.webhook_handler import (
    handle_incoming_message,
    handle_new_waitlist_entry,
    handle_sale_confirmation,
    handle_waitlist_confirmation,
    handle_waitlisted_message,
)

logger = logging.getLogger(__name__)


class SenderStatus(Enum):
    """Status of a message sender."""

    KNOWN_USER = "known_user"
    WAITLISTED = "waitlisted"
    UNKNOWN = "unknown"


def _extract_phone_number(sender: str) -> str:
    """
    Extract and normalize phone number.

    Meta sends plain numbers like '1234567890'.
    Stored numbers may have '+' prefix like '+1234567890'.
    """
    # Remove any whatsapp: prefix (for backwards compatibility)
    if sender.startswith("whatsapp:"):
        sender = sender[9:]
    # Ensure + prefix for database lookup
    if not sender.startswith("+"):
        sender = f"+{sender}"
    return sender


def _lookup_sender(sender: str) -> tuple[SenderStatus, UserProfile | None, WaitlistEntry | None]:
    """
    Lookup sender status and associated profile/waitlist entry.

    Returns:
        Tuple of (status, user_profile, waitlist_entry)
        - KNOWN_USER: has profile, None waitlist entry
        - WAITLISTED: None profile, has waitlist entry
        - UNKNOWN: None profile, None waitlist entry
    """
    phone_number = _extract_phone_number(sender)

    # Check for existing UserProfile first
    try:
        profile = UserProfile.objects.select_related("company").get(phone_number=phone_number)
        return (SenderStatus.KNOWN_USER, profile, None)
    except UserProfile.DoesNotExist:
        pass

    # Check for existing WaitlistEntry
    try:
        entry = WaitlistEntry.objects.get(phone_number=phone_number)
        return (SenderStatus.WAITLISTED, None, entry)
    except WaitlistEntry.DoesNotExist:
        pass

    # Unknown sender
    return (SenderStatus.UNKNOWN, None, None)


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(View):
    """Webhook endpoint for Meta WhatsApp Business Cloud API."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Handle webhook verification from Meta."""
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        verify_token = settings.META_WHATSAPP_VERIFY_TOKEN

        if mode == "subscribe" and token == verify_token:
            logger.info("Webhook verification successful")
            return HttpResponse(challenge, content_type="text/plain")

        logger.warning(f"Webhook verification failed: mode={mode}, token_match={token == verify_token}")
        return HttpResponse("Verification failed", status=403)

    def post(self, request: HttpRequest) -> HttpResponse:
        """Handle incoming webhook from Meta."""
        # Verify signature
        if not self._verify_signature(request):
            logger.warning("Invalid Meta webhook signature")
            return HttpResponse("Invalid signature", status=401)

        # Parse JSON body
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in webhook body: {request.body[:500]}")
            logger.warning(f"Content-Type: {request.content_type}")
            return HttpResponse("Invalid JSON", status=400)

        # Process each entry
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue

                value = change.get("value", {})
                messages = value.get("messages", [])

                for message in messages:
                    self._handle_message(message)

        # Always return 200 OK for Meta webhooks
        return HttpResponse("OK")

    def _handle_message(self, message: dict) -> None:
        """Process a single message from the webhook payload."""
        message_id = message.get("id", "")
        sender = message.get("from", "")  # Plain number like '1234567890'
        message_type = message.get("type", "")

        # Handle interactive button responses
        if message_type == "interactive":
            interactive = message.get("interactive", {})
            if interactive.get("type") == "button_reply":
                button_id = interactive.get("button_reply", {}).get("id", "")
                self._handle_button_response(sender, button_id, message)
                return

        # Handle text messages
        if message_type == "text":
            body = message.get("text", {}).get("body", "")
            if body:
                self._handle_text_message(message_id, sender, body)
            return

        logger.info(f"Ignoring message type: {message_type}")

    def _handle_button_response(self, sender: str, button_id: str, message: dict) -> None:
        """Handle a button click response."""
        logger.info(f"Received button click from {sender}: {button_id}")

        # Get the context (original message being replied to)
        context = message.get("context", {})
        original_message_id = context.get("id", "")

        # Parse button ID to determine action type
        # Format: "confirm_{sale_id}", "cancel_{sale_id}", "waitlist_approve_{entry_id}", "waitlist_reject_{entry_id}"
        if button_id.startswith("confirm_") or button_id.startswith("cancel_"):
            action = "confirm" if button_id.startswith("confirm_") else "cancel"
            handle_sale_confirmation(
                action=action,
                sender=sender,
                original_message_sid=original_message_id or None,
            )
        elif button_id.startswith("waitlist_approve_") or button_id.startswith("waitlist_reject_"):
            action = "approve" if button_id.startswith("waitlist_approve_") else "reject"
            handle_waitlist_confirmation(
                action=action,
                sender=sender,
                original_message_sid=original_message_id or None,
            )
        else:
            logger.warning(f"Unknown button ID format: {button_id}")

    def _handle_text_message(self, message_id: str, sender: str, body: str) -> None:
        """Handle a text message."""
        logger.info(f"Received WhatsApp message from {sender}: {body[:50]}...")

        # Lookup sender status
        status, profile, waitlist_entry = _lookup_sender(sender)

        if status == SenderStatus.UNKNOWN:
            # New user - add to waitlist
            handle_new_waitlist_entry(
                sender=sender,
                text=body,
            )
        elif status == SenderStatus.WAITLISTED:
            # Already on waitlist - send pending message (or capture company name)
            handle_waitlisted_message(
                sender=sender,
                text=body,
                waitlist_entry=waitlist_entry,
            )
        else:
            # Known user - handle the message normally
            handle_incoming_message(
                message_id=message_id,
                sender=sender,
                text=body,
                user_profile=profile,
            )

    def _verify_signature(self, request: HttpRequest) -> bool:
        """Verify the X-Hub-Signature-256 header."""
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        app_secret = settings.META_WHATSAPP_APP_SECRET

        if not app_secret:
            # Skip verification if no app secret configured (dev mode)
            logger.warning("META_WHATSAPP_APP_SECRET not set, skipping signature verification")
            return True

        if not signature_header:
            # TODO: Re-enable after debugging why Meta isn't sending signatures
            logger.warning("X-Hub-Signature-256 header missing, allowing request for debugging")
            return True

        if not signature_header.startswith("sha256="):
            logger.warning(f"Invalid signature format: {signature_header[:20]}...")
            return False

        expected_signature = signature_header[7:]  # Remove 'sha256=' prefix

        # Calculate HMAC-SHA256 of the raw body
        calculated_signature = hmac.new(
            app_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, calculated_signature):
            logger.warning(f"Signature mismatch: expected={expected_signature[:20]}..., calculated={calculated_signature[:20]}...")
            return False

        return True
