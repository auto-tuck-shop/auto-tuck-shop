import hashlib
import hmac
import logging
from base64 import b64encode
from enum import Enum
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import UserProfile, WaitlistEntry
from apps.sales.models import Sale
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
    """Extract phone number from WhatsApp sender format (e.g., 'whatsapp:+1234567890' -> '+1234567890')."""
    if sender.startswith("whatsapp:"):
        return sender[9:]
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
    """Webhook endpoint for Twilio WhatsApp messages."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Handle incoming webhook from Twilio."""
        # Verify signature
        if not self._verify_signature(request):
            logger.warning("Invalid Twilio webhook signature")
            return HttpResponse("Invalid signature", status=401)

        # Extract message data from form POST
        message_sid = request.POST.get("MessageSid", "")
        sender = request.POST.get("From", "")  # e.g., whatsapp:+1234567890
        body = request.POST.get("Body", "")

        # Handle confirm/cancel button clicks (sent as message body)
        body_lower = body.strip().lower()
        if body_lower in ("confirm", "cancel"):
            # Get the original message SID that the user is replying to
            original_message_sid = request.POST.get("OriginalRepliedMessageSid", "")
            logger.info(f"Received {body_lower} from {sender} for message {original_message_sid}")

            # Determine if this is a sale or waitlist confirmation
            is_sale = Sale.objects.filter(confirmation_message_sid=original_message_sid).exists()
            is_waitlist = WaitlistEntry.objects.filter(confirmation_message_sid=original_message_sid).exists()

            if is_waitlist:
                # Map "confirm" to "approve" and "cancel" to "reject" for waitlist
                action = "approve" if body_lower == "confirm" else "reject"
                handle_waitlist_confirmation(
                    action=action,
                    sender=sender,
                    original_message_sid=original_message_sid or None,
                )
            else:
                # Default to sale confirmation (or if neither found, let the handler report error)
                handle_sale_confirmation(
                    action=body_lower,
                    sender=sender,
                    original_message_sid=original_message_sid or None,
                )

            return HttpResponse(
                '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                content_type="application/xml",
            )

        if not body:
            return HttpResponse("OK")

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
                message_id=message_sid,
                sender=sender,
                text=body,
                user_profile=profile,
            )

        # Return empty TwiML response
        return HttpResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            content_type="application/xml",
        )

    def _verify_signature(self, request: HttpRequest) -> bool:
        """Verify the X-Twilio-Signature header."""
        signature = request.headers.get("X-Twilio-Signature", "")
        auth_token = settings.TWILIO_AUTH_TOKEN

        if not auth_token:
            # Skip verification if no auth token configured (dev mode)
            logger.warning("TWILIO_AUTH_TOKEN not set, skipping signature verification")
            return True

        # Build the full URL
        url = request.build_absolute_uri()

        # Get POST parameters sorted by key
        post_data = request.POST.dict()
        sorted_params = "".join(f"{k}{v}" for k, v in sorted(post_data.items()))

        # Create signature
        data = url + sorted_params
        expected_signature = b64encode(
            hmac.new(
                auth_token.encode(),
                data.encode(),
                hashlib.sha1,
            ).digest()
        ).decode()

        return hmac.compare_digest(signature, expected_signature)
