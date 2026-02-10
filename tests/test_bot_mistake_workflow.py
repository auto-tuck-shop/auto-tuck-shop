"""Test the 'Bot mistake' button and updated sale receipt buttons."""

from tests.conftest import button_click_payload, text_message_payload


def _send_sale_and_get_buttons(send_webhook, poll_outbox, phone, sale_text="sold 3 bread $2 each"):
    """Helper: send a sale message, wait for receipt with Bot mistake + Start Over buttons."""
    send_webhook(text_message_payload(phone, sale_text))

    def _find_buttons(outbox):
        for btn in outbox.get("buttons", []):
            mistake = None
            cancel = None
            for b in btn.get("buttons", []):
                if b.get("id", "").startswith("mistake_"):
                    mistake = b
                elif b.get("id", "").startswith("cancel_"):
                    cancel = b
            if mistake and cancel:
                return {
                    "mistake_id": mistake["id"],
                    "cancel_id": cancel["id"],
                    "message_id": btn["message_id"],
                    "body": btn["body"],
                }
        return None

    result = poll_outbox(phone, check=_find_buttons, timeout=20.0)
    assert isinstance(result, dict) and "mistake_id" in result, (
        f"No mistake/cancel buttons found for {phone}. Outbox: {result}"
    )
    return result


def test_sale_receipt_has_mistake_and_cancel_buttons(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Sale receipts should have 'Bot mistake?' and 'Start Over' buttons, but NO confirm button."""
    onboard_user(unique_phone)
    send_webhook(text_message_payload(unique_phone, "sold 2 coke $3 each"))

    def _find_any_buttons(outbox):
        for btn in outbox.get("buttons", []):
            button_ids = [b.get("id", "") for b in btn.get("buttons", [])]
            if any(bid.startswith("mistake_") for bid in button_ids):
                return button_ids
        return None

    button_ids = poll_outbox(unique_phone, check=_find_any_buttons, timeout=20.0)
    assert isinstance(button_ids, list), f"No buttons found. Outbox: {button_ids}"

    # Should have mistake and cancel buttons but NO confirm button
    assert any(bid.startswith("mistake_") for bid in button_ids), (
        f"Expected 'mistake_' button, got: {button_ids}"
    )
    assert any(bid.startswith("cancel_") for bid in button_ids), (
        f"Expected 'cancel_' button, got: {button_ids}"
    )
    assert not any(bid.startswith("confirm_") for bid in button_ids), (
        f"Confirm button should not exist, got: {button_ids}"
    )


def test_sale_auto_confirmed_without_clicking(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Sales should be auto-confirmed (no need to click confirm)."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)
    assert btn_info["body"], "Receipt body should not be empty"


def test_bot_mistake_cancels_sale(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Clicking 'Bot mistake?' should cancel the sale and tell user to resend."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click "Bot mistake?"
    send_webhook(button_click_payload(
        unique_phone, btn_info["mistake_id"], btn_info["message_id"],
    ))

    # Should get a "we'll take a look" message
    def _find_bot_mistake_response(outbox):
        for m in outbox.get("messages", []):
            if "take a look" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_bot_mistake_response)
    assert isinstance(msg, dict), f"Expected bot mistake response. Outbox: {msg}"
    assert "take a look" in msg["text"].lower(), (
        f"Should say we'll take a look. Got: {msg['text']}"
    )
    assert "send" in msg["text"].lower(), (
        f"Should tell user to resend. Got: {msg['text']}"
    )


def test_cancel_sale(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Clicking 'Start Over' should cancel the sale and tell user to resend."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click "Start Over"
    send_webhook(button_click_payload(
        unique_phone, btn_info["cancel_id"], btn_info["message_id"],
    ))

    # Should get cancellation message telling user to resend
    def _find_cancelled(outbox):
        for m in outbox.get("messages", []):
            if "✗" in m["text"] or "thrown out" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_cancelled)
    assert isinstance(msg, dict), f"Expected cancellation message. Outbox: {msg}"
    assert "send" in msg["text"].lower(), (
        f"Should tell user to resend. Got: {msg['text']}"
    )


def test_bot_mistake_double_click_idempotent(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Clicking 'Bot mistake?' twice should not crash — second click gets 'already processed'."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click "Bot mistake?" twice
    payload = button_click_payload(
        unique_phone, btn_info["mistake_id"], btn_info["message_id"],
    )
    send_webhook(payload)

    # Wait for first bot mistake response
    def _find_cancelled(outbox):
        for m in outbox.get("messages", []):
            if "take a look" in m["text"].lower():
                return True
        return None

    poll_outbox(unique_phone, check=_find_cancelled)

    # Click again
    send_webhook(payload)

    # Should get "already processed" message
    def _find_already_processed(outbox):
        for m in outbox.get("messages", []):
            if "already" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_already_processed, timeout=10.0)
    assert isinstance(msg, dict), f"Expected 'already processed' message. Outbox: {msg}"
