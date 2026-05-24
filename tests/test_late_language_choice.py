"""Test that language choice is respected on sale buttons regardless
of whether the user selects language before or after admin approval."""

import time

from tests.conftest import (
    ADMIN_PHONE,
    button_click_payload,
    text_message_payload,
    _poll_outbox,
)


def test_late_language_choice_respected_on_sale_buttons(
    send_webhook, poll_outbox, http_client, staging_url, api_key, app_secret, used_phones, unique_phone
):
    """User selects English AFTER admin approves → sale buttons should still be English."""
    phone = unique_phone
    used_phones.add(ADMIN_PHONE)

    # 1. User sends first message → triggers language buttons
    send_webhook(text_message_payload(phone, "Hi I want to register"))

    # 2. Wait for language buttons — admin notification fires after shop name, not here
    def _find_lang_btn_early(outbox):
        for btn in outbox.get("buttons", []):
            if btn.get("to", "").lstrip("+") == phone.lstrip("+"):
                if any(b["id"].startswith("lang_") for b in btn.get("buttons", [])):
                    return btn
        return None

    lang_early = _poll_outbox(http_client, staging_url, api_key, phone, check=_find_lang_btn_early, timeout=10.0)
    assert isinstance(lang_early, dict), f"No language buttons for {phone}"

    # Send shop name → triggers admin notification (without picking language yet)
    send_webhook(text_message_payload(phone, "Late Lang Shop"))

    # 2. Wait for admin approval button
    def _find_approve(outbox):
        for btn in outbox.get("buttons", []):
            if phone in btn.get("body", ""):
                for b in btn.get("buttons", []):
                    if b["id"].startswith("waitlist_approve_"):
                        return {"button_id": b["id"], "message_id": btn["message_id"]}
        return None

    approve = _poll_outbox(
        http_client, staging_url, api_key, ADMIN_PHONE,
        check=_find_approve, timeout=15.0,
    )
    assert isinstance(approve, dict) and "button_id" in approve, (
        f"No approve button for {phone}. Last: {approve}"
    )

    # 3. Admin approves BEFORE user picks language (the race condition)
    send_webhook(button_click_payload(ADMIN_PHONE, approve["button_id"], approve["message_id"]))

    # 4. Wait for the user to be approved
    def _has_approval(outbox):
        for m in outbox.get("messages", []):
            text = m.get("text", "").lower()
            if "welcome" in text or "approved" in text or "yagamuchirwa" in text:
                return True
        return None

    assert _poll_outbox(
        http_client, staging_url, api_key, phone,
        check=_has_approval, timeout=10.0,
    ) is True, f"User {phone} not approved"

    # 5. NOW the user clicks the English language button (late!)
    def _find_lang_button(outbox):
        for btn in outbox.get("buttons", []):
            for b in btn.get("buttons", []):
                if b["id"].startswith("lang_en_"):
                    return {"button_id": b["id"], "message_id": btn["message_id"]}
        return None

    lang_btn = _poll_outbox(
        http_client, staging_url, api_key, phone,
        check=_find_lang_button, timeout=5.0,
    )
    assert isinstance(lang_btn, dict) and "button_id" in lang_btn, (
        f"No language button for {phone}. Last: {lang_btn}"
    )
    send_webhook(button_click_payload(phone, lang_btn["button_id"], lang_btn["message_id"]))

    # Wait for language confirmation
    def _has_lang_confirmed(outbox):
        for m in outbox.get("messages", []):
            if "language set to english" in m.get("text", "").lower():
                return True
        return None

    assert _poll_outbox(
        http_client, staging_url, api_key, phone,
        check=_has_lang_confirmed, timeout=5.0,
    ) is True, f"Language confirmation not received for {phone}"

    # 6. User sends a sale → buttons should be in English
    send_webhook(text_message_payload(phone, "sold 2 cokes $5 each"))

    def _find_receipt(outbox):
        for b in outbox.get("buttons", []):
            button_ids = [btn.get("id", "") for btn in b.get("buttons", [])]
            if any(bid.startswith("confirm_") for bid in button_ids):
                return b
        return None

    receipt = poll_outbox(phone, check=_find_receipt, timeout=5.0)
    assert isinstance(receipt, dict), f"No receipt for {phone}. Last: {receipt}"

    # The confirm button should say "Looks good" (English), NOT "Ndizvo" (Shona)
    button_titles = {b["title"] for b in receipt["buttons"]}
    assert "Looks good" in button_titles, (
        f"Expected English button 'Looks good' but got: {button_titles}"
    )
    assert "Fix mistake" in button_titles, (
        f"Expected English button 'Fix mistake' but got: {button_titles}"
    )


def test_normal_flow_language_choice_respected(
    send_webhook, poll_outbox, http_client, staging_url, api_key, app_secret, used_phones, unique_phone
):
    """Normal flow: user selects English BEFORE admin approves → sale buttons should be English."""
    phone = unique_phone
    used_phones.add(ADMIN_PHONE)

    # 1. User sends first message → language buttons
    send_webhook(text_message_payload(phone, "Hi I want to register"))

    # 2. User clicks English language button FIRST
    def _find_lang_button(outbox):
        for btn in outbox.get("buttons", []):
            for b in btn.get("buttons", []):
                if b["id"].startswith("lang_en_"):
                    return {"button_id": b["id"], "message_id": btn["message_id"]}
        return None

    lang_btn = _poll_outbox(
        http_client, staging_url, api_key, phone,
        check=_find_lang_button, timeout=5.0,
    )
    assert isinstance(lang_btn, dict) and "button_id" in lang_btn, (
        f"No language button for {phone}. Last: {lang_btn}"
    )
    send_webhook(button_click_payload(phone, lang_btn["button_id"], lang_btn["message_id"]))

    # Wait for language confirmation
    def _has_lang_confirmed(outbox):
        for m in outbox.get("messages", []):
            if "language set to english" in m.get("text", "").lower():
                return True
        return None

    assert _poll_outbox(
        http_client, staging_url, api_key, phone,
        check=_has_lang_confirmed, timeout=5.0,
    ) is True, f"Language confirmation not received for {phone}"

    # 3. User sends shop name → triggers admin notification
    send_webhook(text_message_payload(phone, "Normal Lang Shop"))

    # 4. THEN admin approves
    def _find_approve(outbox):
        for btn in outbox.get("buttons", []):
            if phone in btn.get("body", ""):
                for b in btn.get("buttons", []):
                    if b["id"].startswith("waitlist_approve_"):
                        return {"button_id": b["id"], "message_id": btn["message_id"]}
        return None

    approve = _poll_outbox(
        http_client, staging_url, api_key, ADMIN_PHONE,
        check=_find_approve, timeout=15.0,
    )
    assert isinstance(approve, dict) and "button_id" in approve, (
        f"No approve button for {phone}. Last: {approve}"
    )
    send_webhook(button_click_payload(ADMIN_PHONE, approve["button_id"], approve["message_id"]))

    # 4. Wait for user to be approved
    def _has_approval(outbox):
        for m in outbox.get("messages", []):
            text = m.get("text", "").lower()
            if "welcome" in text or "approved" in text or "yagamuchirwa" in text:
                return True
        return None

    assert _poll_outbox(
        http_client, staging_url, api_key, phone,
        check=_has_approval, timeout=10.0,
    ) is True, f"User {phone} not approved"

    # 5. User sends a sale → buttons should be in English
    send_webhook(text_message_payload(phone, "sold 3 waters $2 each"))

    def _find_receipt(outbox):
        for b in outbox.get("buttons", []):
            button_ids = [btn.get("id", "") for btn in b.get("buttons", [])]
            if any(bid.startswith("confirm_") for bid in button_ids):
                return b
        return None

    receipt = poll_outbox(phone, check=_find_receipt, timeout=5.0)
    assert isinstance(receipt, dict), f"No receipt for {phone}. Last: {receipt}"

    button_titles = {b["title"] for b in receipt["buttons"]}
    assert "Looks good" in button_titles, (
        f"Expected English button 'Looks good' but got: {button_titles}"
    )
    assert "Fix mistake" in button_titles, (
        f"Expected English button 'Fix mistake' but got: {button_titles}"
    )
