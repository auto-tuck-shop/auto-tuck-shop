"""Test onboarding: unknown user → waitlist → admin approve → welcome."""

from tests.conftest import ADMIN_PHONE, text_message_payload, button_click_payload


def test_unknown_user_gets_language_choice(send_webhook, poll_outbox, unique_phone, used_phones):
    """Unknown user's first message triggers language choice buttons
    and an admin notification with approve/reject buttons."""
    used_phones.add(ADMIN_PHONE)

    send_webhook(text_message_payload(unique_phone, "Hello I want to register"))

    # --- User receives language choice buttons ---
    def _has_language_buttons(outbox):
        for btn in outbox.get("buttons", []):
            if btn.get("to", "").lstrip("+") == unique_phone.lstrip("+"):
                button_ids = [b["id"] for b in btn.get("buttons", [])]
                if any(bid.startswith("lang_en_") for bid in button_ids):
                    return btn
        return None

    lang_msg = poll_outbox(unique_phone, check=_has_language_buttons)
    assert isinstance(lang_msg, dict), (
        f"Expected language buttons for {unique_phone}. Outbox: {lang_msg}"
    )

    # Should have 2 buttons: English and Shona
    assert len(lang_msg["buttons"]) == 2
    button_ids = [b["id"] for b in lang_msg["buttons"]]
    assert any(bid.startswith("lang_en_") for bid in button_ids)
    assert any(bid.startswith("lang_sn_") for bid in button_ids)

    # Body should contain the bilingual prompt
    assert "language" in lang_msg["body"].lower() or "mutauro" in lang_msg["body"].lower()

    # --- Admin notification fires after language selection + shop name ---
    # Click English
    en_button = next(b for b in lang_msg["buttons"] if b["id"].startswith("lang_en_"))
    send_webhook(button_click_payload(unique_phone, en_button["id"], lang_msg["message_id"]))

    # Send shop name → triggers admin notification
    send_webhook(text_message_payload(unique_phone, "My Test Shop"))

    phone_plain = unique_phone.lstrip("+")

    def _find_admin_notification(outbox):
        for btn in outbox.get("buttons", []):
            if phone_plain in btn.get("body", "") or unique_phone in btn.get("body", ""):
                return btn
        return None

    admin_btn = poll_outbox(ADMIN_PHONE, check=_find_admin_notification)
    assert isinstance(admin_btn, dict) and "body" in admin_btn, (
        f"Expected admin notification for {unique_phone}. Outbox: {admin_btn}"
    )

    assert phone_plain in admin_btn["body"] or unique_phone in admin_btn["body"]

    # Should have exactly 2 buttons: approve and reject
    assert len(admin_btn["buttons"]) == 2
    button_ids = [b["id"] for b in admin_btn["buttons"]]
    assert any(bid.startswith("waitlist_approve_") for bid in button_ids)
    assert any(bid.startswith("waitlist_reject_") for bid in button_ids)


def test_language_selection_english(send_webhook, poll_outbox, unique_phone, used_phones):
    """User selects English → gets confirmation and waitlist welcome in English."""
    used_phones.add(ADMIN_PHONE)

    send_webhook(text_message_payload(unique_phone, "Hi"))

    # Wait for language buttons
    def _has_language_buttons(outbox):
        for btn in outbox.get("buttons", []):
            if btn.get("to", "").lstrip("+") == unique_phone.lstrip("+"):
                button_ids = [b["id"] for b in btn.get("buttons", [])]
                if any(bid.startswith("lang_en_") for bid in button_ids):
                    return btn
        return None

    lang_msg = poll_outbox(unique_phone, check=_has_language_buttons)
    assert isinstance(lang_msg, dict), f"No language buttons found. Outbox: {lang_msg}"

    # Find the English button and click it
    en_button = next(b for b in lang_msg["buttons"] if b["id"].startswith("lang_en_"))
    send_webhook(button_click_payload(unique_phone, en_button["id"], lang_msg["message_id"]))

    # Should receive confirmation + waitlist welcome in English
    def _has_english_waitlist(outbox):
        msgs = outbox.get("messages", [])
        for m in msgs:
            text = m.get("text", "").lower()
            if "waitlist" in text or "added" in text:
                return m
        return None

    msg = poll_outbox(unique_phone, check=_has_english_waitlist)
    assert isinstance(msg, dict), f"Expected English waitlist message. Outbox: {msg}"
    assert "waitlist" in msg["text"].lower()


def test_language_selection_shona(send_webhook, poll_outbox, unique_phone, used_phones):
    """User selects Shona → gets confirmation and waitlist welcome in Shona."""
    used_phones.add(ADMIN_PHONE)

    send_webhook(text_message_payload(unique_phone, "Hi"))

    # Wait for language buttons
    def _has_language_buttons(outbox):
        for btn in outbox.get("buttons", []):
            if btn.get("to", "").lstrip("+") == unique_phone.lstrip("+"):
                button_ids = [b["id"] for b in btn.get("buttons", [])]
                if any(bid.startswith("lang_sn_") for bid in button_ids):
                    return btn
        return None

    lang_msg = poll_outbox(unique_phone, check=_has_language_buttons)
    assert isinstance(lang_msg, dict), f"No language buttons found. Outbox: {lang_msg}"

    # Find the Shona button and click it
    sn_button = next(b for b in lang_msg["buttons"] if b["id"].startswith("lang_sn_"))
    send_webhook(button_click_payload(unique_phone, sn_button["id"], lang_msg["message_id"]))

    # Should receive confirmation + waitlist welcome in Shona
    def _has_shona_waitlist(outbox):
        msgs = outbox.get("messages", [])
        for m in msgs:
            text = m.get("text", "").lower()
            if "pawaitlist" in text or "mwaiswa" in text:
                return m
        return None

    msg = poll_outbox(unique_phone, check=_has_shona_waitlist)
    assert isinstance(msg, dict), f"Expected Shona waitlist message. Outbox: {msg}"


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
