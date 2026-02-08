"""Test sale confirmation: send sale → click confirm button → sale confirmed."""

from tests.conftest import button_click_payload, text_message_payload


def test_confirm_sale(send_webhook, poll_outbox, onboard_user, unique_phone):
    """User sends sale, gets confirmation buttons, clicks confirm, gets confirmed message."""
    onboard_user(unique_phone)

    # Send a sale
    send_webhook(text_message_payload(unique_phone, "sold 3 bread $2 each"))

    # Wait for confirmation buttons
    def _find_confirm_button(outbox):
        for btn in outbox.get("buttons", []):
            for b in btn.get("buttons", []):
                if "confirm" in b.get("id", "").lower():
                    return {"button_id": b["id"], "message_id": btn["message_id"]}
        return None

    btn_info = poll_outbox(unique_phone, check=_find_confirm_button, timeout=20.0)
    assert isinstance(btn_info, dict) and "button_id" in btn_info, (
        f"No confirm button found for {unique_phone}. Outbox: {btn_info}"
    )

    # Click confirm
    send_webhook(button_click_payload(unique_phone, btn_info["button_id"], btn_info["message_id"]))

    # Wait for "confirmed" message
    def _has_confirmed(outbox):
        for m in outbox.get("messages", []):
            if "confirmed" in m["text"].lower():
                return True
        return None

    result = poll_outbox(unique_phone, check=_has_confirmed)
    assert result is True, f"Expected 'confirmed' message for {unique_phone}. Outbox: {result}"
