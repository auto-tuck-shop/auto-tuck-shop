"""Client for sending messages via Meta WhatsApp Business Cloud API."""

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

META_GRAPH_API_URL = "https://graph.facebook.com/v21.0"


class WhatsAppClient:
    """Client for Meta WhatsApp Business Cloud API."""

    def __init__(self):
        self.access_token = settings.META_WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID

    def _get_api_url(self) -> str:
        return f"{META_GRAPH_API_URL}/{self.phone_number_id}/messages"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _normalize_phone_number(self, phone: str) -> str:
        """
        Normalize phone number for Meta API.

        Meta expects plain numbers without 'whatsapp:' prefix and without '+'.
        e.g., '1234567890' or '27821234567'
        """
        # Remove whatsapp: prefix if present
        if phone.startswith("whatsapp:"):
            phone = phone[9:]
        # Remove + prefix if present
        if phone.startswith("+"):
            phone = phone[1:]
        return phone

    async def send_message_with_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict[str, str]],
        reply_to: str | None = None,
    ) -> str | None:
        """
        Send a WhatsApp message with interactive buttons via Meta Cloud API.

        Args:
            to: The recipient's phone number (e.g., +1234567890 or whatsapp:+1234567890)
            body: The message body text
            buttons: List of button dicts with 'title' and 'id' keys
            reply_to: Optional message ID to reply to (for quoted replies)

        Returns:
            The message ID if successful, None otherwise
        """
        if not self.access_token or not self.phone_number_id:
            logger.error("Meta WhatsApp credentials not configured")
            return None

        to_number = self._normalize_phone_number(to)

        # Build interactive message with buttons (max 3 buttons)
        button_objects = [
            {
                "type": "reply",
                "reply": {
                    "id": btn["id"],
                    "title": btn["title"][:20],  # Max 20 chars for button title
                }
            }
            for btn in buttons[:3]
        ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": body[:1024],  # Max 1024 chars for body
                },
                "action": {
                    "buttons": button_objects,
                }
            }
        }

        # Add reply context if provided
        if reply_to:
            payload["context"] = {"message_id": reply_to}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self._get_api_url(),
                    headers=self._get_headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                message_id = data.get("messages", [{}])[0].get("id")
                logger.info(f"Sent WhatsApp button message to {to_number}, id={message_id}")
                return message_id
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Failed to send WhatsApp button message: {e.response.status_code} - {e.response.text}"
                )
                return None
            except httpx.RequestError as e:
                logger.error(f"Meta WhatsApp request error: {e}")
                return None

    async def send_message(self, to: str, text: str) -> bool:
        """
        Send a WhatsApp message via Meta Cloud API.

        Args:
            to: The recipient's phone number (e.g., +1234567890 or whatsapp:+1234567890)
            text: The message text

        Returns:
            True if successful, False otherwise
        """
        if not self.access_token or not self.phone_number_id:
            logger.error("Meta WhatsApp credentials not configured")
            return False

        to_number = self._normalize_phone_number(to)

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text,
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self._get_api_url(),
                    headers=self._get_headers(),
                    json=payload,
                )
                response.raise_for_status()
                logger.info(f"Sent WhatsApp message to {to_number}")
                return True
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Failed to send WhatsApp message: {e.response.status_code} - {e.response.text}"
                )
                return False
            except httpx.RequestError as e:
                logger.error(f"Meta WhatsApp request error: {e}")
                return False
