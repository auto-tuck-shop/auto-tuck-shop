"""Test audio message sale: upload mock audio → send audio webhook → get response."""

from tests.conftest import audio_message_payload


def test_audio_sale_gets_response(
    send_webhook, poll_outbox, onboard_user, unique_phone, upload_mock_media, r2_audio
):
    """Onboarded user sends audio and gets some response (transcription + parse)."""
    onboard_user(unique_phone)

    audio_bytes, mime_type = r2_audio()
    media_id = f"media_{unique_phone.lstrip('+')}"
    upload_mock_media(media_id, audio_bytes, mime_type)

    send_webhook(audio_message_payload(unique_phone, media_id))

    def _has_any_response(outbox):
        # After onboarding there's already a welcome message, so look for >1
        total = len(outbox.get("messages", [])) + len(outbox.get("buttons", []))
        if total > 1:
            return True
        return None

    result = poll_outbox(unique_phone, check=_has_any_response, timeout=25.0)
    assert result is True, f"Expected response after audio for {unique_phone}. Outbox: {result}"
