"""Test sale confirmation and cancellation flows."""

from tests.conftest import button_click_payload, text_message_payload


def _send_sale_and_get_buttons(send_webhook, poll_outbox, phone, sale_text="sold 3 bread $2 each"):
    """Helper: send a sale message, wait for confirmation buttons, return button info."""
    send_webhook(text_message_payload(phone, sale_text))

    def _find_buttons(outbox):
        for btn in outbox.get("buttons", []):
            confirm = None
            cancel = None
            for b in btn.get("buttons", []):
                if b.get("id", "").startswith("confirm_"):
                    confirm = b
                elif b.get("id", "").startswith("cancel_"):
                    cancel = b
            if confirm and cancel:
                return {
                    "confirm_id": confirm["id"],
                    "cancel_id": cancel["id"],
                    "message_id": btn["message_id"],
                    "body": btn["body"],
                }
        return None

    result = poll_outbox(phone, check=_find_buttons, timeout=20.0)
    assert isinstance(result, dict) and "confirm_id" in result, (
        f"No confirm/cancel buttons found for {phone}. Outbox: {result}"
    )
    return result


def test_confirm_sale(send_webhook, poll_outbox, onboard_user, unique_phone):
    """User sends sale, clicks confirm, gets '✓ Confirmed' reply."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click confirm
    send_webhook(button_click_payload(
        unique_phone, btn_info["confirm_id"], btn_info["message_id"],
    ))

    # Wait for confirmed message
    def _find_confirmed(outbox):
        for m in outbox.get("messages", []):
            if "✓" in m["text"] and "confirmed" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_confirmed)
    assert isinstance(msg, dict), f"Expected '✓ Confirmed' message. Outbox: {msg}"

    # Confirmed message should be short (just the confirmation, not a re-parse)
    assert len(msg["text"]) < 50, f"Confirmed message unexpectedly long: {msg['text']}"


def test_cancel_sale(send_webhook, poll_outbox, onboard_user, unique_phone):
    """User sends sale, clicks 'Start Over', gets cancellation message."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click cancel (Start Over)
    send_webhook(button_click_payload(
        unique_phone, btn_info["cancel_id"], btn_info["message_id"],
    ))

    # Wait for cancellation message
    def _find_cancelled(outbox):
        for m in outbox.get("messages", []):
            if "✗" in m["text"] or "thrown out" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_cancelled)
    assert isinstance(msg, dict), f"Expected cancellation message. Outbox: {msg}"

    # Should tell them to resend
    assert "send" in msg["text"].lower(), (
        f"Cancellation message should tell user to resend. Got: {msg['text']}"
    )


def test_double_confirm_is_idempotent(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Clicking confirm twice should not crash — second click gets 'already processed'."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click confirm twice
    payload = button_click_payload(
        unique_phone, btn_info["confirm_id"], btn_info["message_id"],
    )
    send_webhook(payload)

    # Wait for first confirmed message
    def _find_confirmed(outbox):
        for m in outbox.get("messages", []):
            if "✓" in m["text"] and "confirmed" in m["text"].lower():
                return True
        return None

    poll_outbox(unique_phone, check=_find_confirmed)

    # Click again
    send_webhook(payload)

    # Wait for "already processed" message
    def _find_already_processed(outbox):
        for m in outbox.get("messages", []):
            if "already" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_already_processed, timeout=10.0)
    assert isinstance(msg, dict), f"Expected 'already processed' message. Outbox: {msg}"
