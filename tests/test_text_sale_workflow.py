"""Test text message sale: send sale text → get confirmation buttons."""

from tests.conftest import text_message_payload


def test_text_sale_sends_confirmation(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Onboarded user sends a sale message and gets confirmation buttons."""
    onboard_user(unique_phone)

    send_webhook(text_message_payload(unique_phone, "sold 2 cokes $5 each"))

    def _has_confirmation_buttons(outbox):
        for b in outbox.get("buttons", []):
            button_text = str(b.get("buttons", [])).lower()
            if "confirm" in button_text:
                return True
        return None

    result = poll_outbox(unique_phone, check=_has_confirmation_buttons, timeout=20.0)
    assert result is True, f"Expected confirmation buttons for {unique_phone}. Outbox: {result}"
