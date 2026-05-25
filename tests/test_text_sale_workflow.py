"""Test text message sale: send sale text → get receipt buttons with parsed items."""

import re

from tests.conftest import text_message_payload


def test_text_sale_sends_receipt(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Onboarded user sends a sale message and gets receipt buttons
    whose body reflects the parsed items, quantities, and prices."""
    onboard_user(unique_phone)

    send_webhook(text_message_payload(unique_phone, "sold 2 cokes $5 each"))

    # --- Wait for the receipt button message ---
    def _find_receipt(outbox):
        for b in outbox.get("buttons", []):
            button_ids = [btn.get("id", "") for btn in b.get("buttons", [])]
            if any(bid.startswith("confirm_") for bid in button_ids):
                return b
        return None

    btn_msg = poll_outbox(unique_phone, check=_find_receipt, timeout=10.0)
    assert isinstance(btn_msg, dict), (
        f"Expected receipt buttons for {unique_phone}. Outbox: {btn_msg}"
    )

    body = btn_msg["body"].lower()

    # The receipt body should contain the parsed item details
    assert "coke" in body, f"Expected 'coke' in receipt body: {btn_msg['body']}"
    assert "2" in body, f"Expected quantity '2' in receipt body: {btn_msg['body']}"

    # Should contain a price ($ or dollar amount)
    assert "$" in btn_msg["body"] or "5" in body, (
        f"Expected price in receipt body: {btn_msg['body']}"
    )

    # --- Button structure ---
    buttons = btn_msg["buttons"]
    assert len(buttons) == 2, f"Expected 2 buttons (confirm + fix), got {len(buttons)}"

    button_ids = {b["id"] for b in buttons}
    confirm_ids = [bid for bid in button_ids if bid.startswith("confirm_")]
    fix_ids = [bid for bid in button_ids if bid.startswith("fix_")]
    assert len(confirm_ids) == 1, f"Expected one confirm_<id> button, got {button_ids}"
    assert len(fix_ids) == 1, f"Expected one fix_<id> button, got {button_ids}"

    # Both buttons should reference the same sale ID
    confirm_sale_id = confirm_ids[0].split("_", 1)[1]
    fix_sale_id = fix_ids[0].split("_", 1)[1]
    assert confirm_sale_id == fix_sale_id, (
        f"Confirm and fix reference different sales: {confirm_sale_id} vs {fix_sale_id}"
    )

    # --- Reply threading ---
    # The receipt should be threaded as a reply to the user's original message
    assert btn_msg.get("reply_to") is not None, (
        f"Expected receipt to be a reply (reply_to set), got: {btn_msg}"
    )

    # --- No extra messages (no errors, no duplicates) ---
    outbox = poll_outbox(unique_phone, check=lambda ob: ob if ob.get("buttons") else None)
    # Should have exactly 1 button message (the receipt) beyond the welcome
    non_welcome_buttons = [
        b for b in outbox["buttons"]
        if not any(kw in b.get("body", "").lower() for kw in ("waitlist", "welcome", "approved"))
    ]
    assert len(non_welcome_buttons) == 1, (
        f"Expected exactly 1 sale receipt, got {len(non_welcome_buttons)}: {non_welcome_buttons}"
    )
