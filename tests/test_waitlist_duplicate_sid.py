"""
Test: Waitlist approval works correctly even when the mock outbox is cleared
between registrations.

Regression test for PYTHON-DJANGO-C / PYTHON-DJANGO-6:
  WaitlistEntry.MultipleObjectsReturned: get() returned more than one WaitlistEntry

Root cause was MockWhatsAppClient generating counter-based message IDs that
collided after outbox reset. Fixed by using UUIDs for mock message IDs,
matching Meta's behavior of providing globally unique IDs.
"""

import random

from tests.conftest import ADMIN_PHONE, text_message_payload, button_click_payload


def _find_approve_button_for_phone(phone, outbox):
    """Find the approve button in admin outbox for a specific phone."""
    for btn in outbox.get("buttons", []):
        if phone in btn.get("body", ""):
            for b in btn.get("buttons", []):
                if b["id"].startswith("waitlist_approve_"):
                    return {"button_id": b["id"], "message_id": btn["message_id"]}
    return None


def test_waitlist_approval_after_outbox_reset(
    send_webhook, poll_outbox, get_outbox, http_client, staging_url, api_key,
):
    """
    Two users register with an outbox reset in between. Approving the second
    user should succeed — mock message IDs must be unique across resets.
    """
    phone_a = f"+2783{random.randint(1000000, 9999999)}"
    phone_b = f"+2783{random.randint(1000000, 9999999)}"

    # 0. Clear outbox to start clean
    resp = http_client.delete(
        f"{staging_url}/test/outbox/",
        headers={"X-Test-Api-Key": api_key},
    )
    assert resp.status_code == 200

    # 1. Register phone A — language prompt fires first, then shop name triggers admin notification
    send_webhook(text_message_payload(phone_a, "Hello"))
    # Wait for language buttons then send shop name
    def _lang_a(ob):
        for btn in ob.get("buttons", []):
            if btn.get("to", "").lstrip("+") == phone_a.lstrip("+"):
                if any(b["id"].startswith("lang_") for b in btn.get("buttons", [])):
                    return btn
        return None
    lang_a = poll_outbox(phone_a, check=_lang_a, timeout=10.0)
    assert isinstance(lang_a, dict), f"No language buttons for phone_a={phone_a}"
    send_webhook(text_message_payload(phone_a, "Shop A"))

    result_a = poll_outbox(
        ADMIN_PHONE,
        check=lambda ob: _find_approve_button_for_phone(phone_a.lstrip("+"), ob),
        timeout=10.0,
    )
    assert isinstance(result_a, dict) and "message_id" in result_a, (
        f"No approve button found for phone_a={phone_a}. Got: {result_a}"
    )

    # 2. Clear outbox — resets MockWhatsAppClient state
    resp = http_client.delete(
        f"{staging_url}/test/outbox/",
        headers={"X-Test-Api-Key": api_key},
    )
    assert resp.status_code == 200

    # 3. Register phone B — same flow
    send_webhook(text_message_payload(phone_b, "Hello"))
    def _lang_b(ob):
        for btn in ob.get("buttons", []):
            if btn.get("to", "").lstrip("+") == phone_b.lstrip("+"):
                if any(b["id"].startswith("lang_") for b in btn.get("buttons", [])):
                    return btn
        return None
    lang_b = poll_outbox(phone_b, check=_lang_b, timeout=10.0)
    assert isinstance(lang_b, dict), f"No language buttons for phone_b={phone_b}"
    send_webhook(text_message_payload(phone_b, "Shop B"))

    result_b = poll_outbox(
        ADMIN_PHONE,
        check=lambda ob: _find_approve_button_for_phone(phone_b.lstrip("+"), ob),
        timeout=10.0,
    )
    assert isinstance(result_b, dict) and "message_id" in result_b, (
        f"No approve button found for phone_b={phone_b}. Got: {result_b}"
    )

    # Message IDs should be unique (no longer collide after UUID fix)
    assert result_a["message_id"] != result_b["message_id"], (
        f"Mock message IDs should be unique but both are {result_a['message_id']}"
    )

    # 4. Approve phone B
    approve_payload = button_click_payload(
        ADMIN_PHONE, result_b["button_id"], result_b["message_id"]
    )
    send_webhook(approve_payload)

    # 5. Phone B should receive the approval message
    def _has_approval_msg(outbox):
        for m in outbox.get("messages", []):
            text = m.get("text", "")
            if "yagamuchirwa" in text or "Shop:" in text:
                return True
        return None

    result = poll_outbox(phone_b, check=_has_approval_msg, timeout=10.0)
    assert result is True, (
        f"Phone B ({phone_b}) was not approved. "
        f"Outbox: {get_outbox(phone_b)}"
    )
