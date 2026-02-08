import logging

logger = logging.getLogger(__name__)


class MockWhatsAppClient:
    """
    Mock WhatsApp client for staging/testing.

    Implements same interface as WhatsAppClient but doesn't send real messages.
    Tracks all sent messages for test assertions.
    """

    # Class-level storage for verification
    sent_messages = []
    sent_buttons = []
    media_downloads = {}  # media_id -> (audio_data, mime_type)

    async def send_message(self, to: str, text: str) -> bool:
        """Mock send_message - logs but doesn't send."""
        logger.info(f"[MOCK] Would send WhatsApp message to {to}: {text[:50]}...")
        self.sent_messages.append({"to": to, "text": text})
        return True

    async def send_message_with_buttons(
        self, to: str, body: str, buttons: list, reply_to: str | None = None
    ) -> str | None:
        """Mock send_message_with_buttons - logs but doesn't send."""
        message_id = f"wamid.mock_{len(self.sent_buttons)}"
        logger.info(f"[MOCK] Would send button message to {to}: {body[:50]}...")
        self.sent_buttons.append({
            "to": to,
            "body": body,
            "buttons": buttons,
            "message_id": message_id,
            "reply_to": reply_to
        })
        return message_id

    async def download_media(self, media_id: str) -> tuple[bytes, str] | None:
        """
        Mock download_media - returns pre-configured test data.

        For tests, populate media_downloads with real audio files:
        MockWhatsAppClient.media_downloads["media_123"] = (audio_bytes, "audio/ogg")
        """
        if media_id in self.media_downloads:
            audio_data, mime_type = self.media_downloads[media_id]
            logger.info(f"[MOCK] Returning test audio for {media_id}: {len(audio_data)} bytes")
            return (audio_data, mime_type)

        logger.warning(f"[MOCK] No test audio configured for {media_id}, returning empty")
        return (b"", "audio/ogg")

    async def get_media_url(self, media_id: str) -> tuple[str, str] | None:
        """Mock get_media_url - returns mock URL."""
        return (f"https://mock-cdn.example.com/{media_id}.ogg", "audio/ogg")

    @classmethod
    def reset(cls):
        """Reset state between tests."""
        cls.sent_messages = []
        cls.sent_buttons = []
        cls.media_downloads = {}
