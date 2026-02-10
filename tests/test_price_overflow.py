"""Test that extremely large prices are rejected gracefully instead of causing a DB error."""

from tests.conftest import text_message_payload


def test_price_overflow_returns_friendly_error(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Sending a sale with an absurdly large price should return a helpful message,
    not crash with a DataError (numeric field overflow)."""
    onboard_user(unique_phone)

    # Send a message with a price that exceeds DecimalField(max_digits=10, decimal_places=2)
    send_webhook(text_message_payload(unique_phone, "sold an apple for a billion dollars"))

    # Should get a friendly error message, NOT a receipt and NOT a 500 error
    def _find_overflow_response(outbox):
        for m in outbox.get("messages", []):
            text = m.get("text", "").lower()
            if "too large" in text or "double-check" in text:
                return m
        return None

    result = poll_outbox(unique_phone, check=_find_overflow_response, timeout=20.0)
    assert isinstance(result, dict), (
        f"Expected a 'price too large' message for {unique_phone}. "
        f"Got: {result}"
    )
