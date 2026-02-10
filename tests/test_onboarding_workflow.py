"""Test onboarding: unknown user → waitlist → admin approve → welcome."""

from tests.conftest import ADMIN_PHONE, text_message_payload


def test_unknown_user_gets_waitlisted(send_webhook, poll_outbox, unique_phone, used_phones):
    """Unknown user's first message triggers a waitlist message to them
    and an admin notification with approve/reject buttons."""
    used_phones.add(ADMIN_PHONE)

    send_webhook(text_message_payload(unique_phone, "Hello I want to register"))

    # --- User receives the waitlist welcome ---
    def _has_waitlist_msg(outbox):
        for m in outbox.get("messages", []):
            if "waitlist" in m["text"].lower():
                return m
        return None

    msg = poll_outbox(unique_phone, check=_has_waitlist_msg)
    assert isinstance(msg, dict), f"Expected waitlist message for {unique_phone}. Outbox: {msg}"

    # The message should be the full waitlist welcome, not a fragment
    assert "yagamuchirwa" in msg["text"].lower() or "waitlist" in msg["text"].lower()

    # Exactly 1 message to the user (no duplicates, no errors)
    outbox = poll_outbox(unique_phone, check=lambda ob: ob if ob.get("messages") else None)
    assert len(outbox["messages"]) == 1, (
        f"Expected exactly 1 message to {unique_phone}, got {len(outbox['messages'])}"
    )

    # --- Admin receives a notification with the user's phone and first message ---
    phone_plain = unique_phone.lstrip("+")

    def _find_admin_notification(outbox):
        for btn in outbox.get("buttons", []):
            if phone_plain in btn.get("body", "") or unique_phone in btn.get("body", ""):
                return btn
        return None

    admin_btn = poll_outbox(ADMIN_PHONE, check=_find_admin_notification)
    assert isinstance(admin_btn, dict), (
        f"Expected admin notification for {unique_phone}. Outbox: {admin_btn}"
    )

    # Admin notification body should include the user's phone and their first message
    assert phone_plain in admin_btn["body"] or unique_phone in admin_btn["body"]
    assert "register" in admin_btn["body"].lower()

    # Should have exactly 2 buttons: approve and reject
    assert len(admin_btn["buttons"]) == 2
    button_ids = [b["id"] for b in admin_btn["buttons"]]
    assert any(bid.startswith("waitlist_approve_") for bid in button_ids)
    assert any(bid.startswith("waitlist_reject_") for bid in button_ids)


def test_full_onboarding(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Full flow: register → admin approve → user gets welcome message with company name."""
    onboard_user(unique_phone)

    def _find_approval_welcome(outbox):
        for m in outbox.get("messages", []):
            text = m["text"]
            # The approval welcome contains "Shop:" with the company name
            if "Shop:" in text or "Company:" in text:
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_approval_welcome)
    assert isinstance(msg, dict), f"Expected approval welcome for {unique_phone}. Outbox: {msg}"

    # Should contain the company name line
    assert "Shop:" in msg["text"] or "Company:" in msg["text"]

    # Should contain onboarding instructions (how to record sales)
    assert "voice note" in msg["text"].lower() or "sold" in msg["text"].lower()
