"""Client for sending messages via Twilio WhatsApp API."""

import json
import logging
from base64 import b64encode

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TWILIO_API_URL = "https://api.twilio.com/2010-04-01"


class WhatsAppClient:
    """Client for Twilio WhatsApp API."""

    def __init__(self):
        self.account_sid = settings.TWILIO_ACCOUNT_SID
        self.auth_token = settings.TWILIO_AUTH_TOKEN
        self.from_number = settings.TWILIO_WHATSAPP_NUMBER

    def _get_auth_header(self) -> str:
        credentials = f"{self.account_sid}:{self.auth_token}"
        encoded = b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    async def send_message_with_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict[str, str]],
        reply_to: str | None = None,
    ) -> str | None:
        """
        Send a WhatsApp message with interactive buttons via Twilio Content API.

        Requires TWILIO_SALE_CONFIRM_CONTENT_SID to be set in settings, pointing
        to a Content Template with quick reply buttons configured in Twilio console.

        Args:
            to: The recipient's WhatsApp number (e.g., whatsapp:+1234567890)
            body: The message body text
            buttons: List of button dicts with 'title' and 'id' keys
            reply_to: Optional message SID to reply to (for quoted replies)

        Returns:
            The message SID if successful, None otherwise
        """
        content_sid = getattr(settings, "TWILIO_SALE_CONFIRM_CONTENT_SID", None)

        if not content_sid:
            logger.error("TWILIO_SALE_CONFIRM_CONTENT_SID not configured")
            return None

        if not self.account_sid or not self.auth_token:
            logger.error("Twilio credentials not configured")
            return None

        url = f"{TWILIO_API_URL}/Accounts/{self.account_sid}/Messages.json"

        if not to.startswith("whatsapp:"):
            to = f"whatsapp:{to}"

        # Content variables for the template (maps to {{1}}, {{2}}, etc.)
        content_variables = json.dumps({
            "1": body,  # Message body
            **{str(i + 2): btn["id"] for i, btn in enumerate(buttons[:3])}
        })

        payload = {
            "From": self.from_number,
            "To": to,
            "ContentSid": content_sid,
            "ContentVariables": content_variables,
        }

        # Try to add reply context if provided
        if reply_to:
            # Twilio may support this parameter for WhatsApp quoted replies
            payload["Context"] = reply_to
            logger.info(f"Adding reply context: {reply_to}")

        headers = {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=headers, data=payload)
                response.raise_for_status()
                data = response.json()
                message_sid = data.get("sid")
                logger.info(f"Sent WhatsApp button message to {to}, sid={message_sid}")
                return message_sid
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Failed to send WhatsApp button message: {e.response.status_code} - {e.response.text}"
                )
                return None
            except httpx.RequestError as e:
                logger.error(f"Twilio request error: {e}")
                return None

    async def send_message(self, to: str, text: str) -> bool:
        """
        Send a WhatsApp message via Twilio.

        Args:
            to: The recipient's WhatsApp number (e.g., whatsapp:+1234567890)
            text: The message text

        Returns:
            True if successful, False otherwise
        """
        if not self.account_sid or not self.auth_token:
            logger.error("Twilio credentials not configured")
            return False

        url = f"{TWILIO_API_URL}/Accounts/{self.account_sid}/Messages.json"

        # Ensure the 'to' number has whatsapp: prefix
        if not to.startswith("whatsapp:"):
            to = f"whatsapp:{to}"

        payload = {
            "From": self.from_number,
            "To": to,
            "Body": text,
        }

        headers = {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=headers, data=payload)
                response.raise_for_status()
                logger.info(f"Sent WhatsApp message to {to}")
                return True
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Failed to send WhatsApp message: {e.response.status_code} - {e.response.text}"
                )
                return False
            except httpx.RequestError as e:
                logger.error(f"Twilio request error: {e}")
                return False
