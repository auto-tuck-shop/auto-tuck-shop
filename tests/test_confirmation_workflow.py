"""Test sale confirmation and fix flows."""

from tests.conftest import button_click_payload, text_message_payload


def _send_sale_and_get_buttons(send_webhook, poll_outbox, phone, sale_text="sold 3 bread $2 each"):
    """Helper: send a sale message, wait for receipt buttons, return button info."""
    send_webhook(text_message_payload(phone, sale_text))

    def _find_buttons(outbox):
        for btn in outbox.get("buttons", []):
            confirm = None
            fix = None
            for b in btn.get("buttons", []):
                if b.get("id", "").startswith("confirm_"):
                    confirm = b
                elif b.get("id", "").startswith("fix_"):
                    fix = b
            if confirm and fix:
                return {
                    "confirm_id": confirm["id"],
                    "fix_id": fix["id"],
                    "message_id": btn["message_id"],
                    "body": btn["body"],
                }
        return None

    result = poll_outbox(phone, check=_find_buttons, timeout=10.0)
    assert isinstance(result, dict) and "fix_id" in result, (
        f"No buttons found for {phone}. Outbox: {result}"
    )
    return result


def test_fix_sale(send_webhook, poll_outbox, onboard_user, unique_phone):
    """User sends sale, clicks 'Fix mistake', gets cancellation message."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click fix (Fix mistake)
    send_webhook(button_click_payload(
        unique_phone, btn_info["fix_id"], btn_info["message_id"],
    ))

    # Wait for fix response
    def _find_fix_response(outbox):
        for m in outbox.get("messages", []):
            if "send" in m["text"].lower() and "again" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_fix_response)
    assert isinstance(msg, dict), f"Expected fix response. Outbox: {msg}"

    # Should tell them to resend
    assert "send" in msg["text"].lower(), (
        f"Fix message should tell user to resend. Got: {msg['text']}"
    )


def test_double_fix_is_idempotent(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Clicking fix twice should not crash — second click gets 'already processed'."""
    onboard_user(unique_phone)

    btn_info = _send_sale_and_get_buttons(send_webhook, poll_outbox, unique_phone)

    # Click fix twice
    payload = button_click_payload(
        unique_phone, btn_info["fix_id"], btn_info["message_id"],
    )
    send_webhook(payload)

    # Wait for first fix message
    def _find_fix_response(outbox):
        for m in outbox.get("messages", []):
            if "send" in m["text"].lower() and "again" in m["text"].lower():
                return True
        return None

    poll_outbox(unique_phone, check=_find_fix_response)

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
