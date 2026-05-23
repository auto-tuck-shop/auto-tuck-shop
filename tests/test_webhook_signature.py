"""Test webhook signature verification — should reject missing headers."""

import json
import httpx
import pytest

from tests.conftest import text_message_payload


def test_webhook_missing_signature_returns_401(http_client, staging_url):
    """Webhook without X-Hub-Signature-256 header should be rejected with 401."""
    payload = text_message_payload("5551234567", "Hello")
    body = json.dumps(payload).encode()
    
    # Send without signature header
    resp = http_client.post(
        f"{staging_url}/webhook/whatsapp/",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    
    # Should be rejected with 401 Unauthorized
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


def test_webhook_invalid_signature_returns_401(http_client, staging_url):
    """Webhook with invalid signature should be rejected with 401."""
    payload = text_message_payload("5551234567", "Hello")
    body = json.dumps(payload).encode()
    
    # Send with invalid signature
    resp = http_client.post(
        f"{staging_url}/webhook/whatsapp/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=invalid_signature_here",
        },
    )
    
    # Should be rejected with 401 Unauthorized
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


def test_webhook_valid_signature_returns_200(send_webhook, poll_outbox, unique_phone, used_phones):
    """Webhook with valid signature should be accepted with 200."""
    from tests.conftest import ADMIN_PHONE
    
    used_phones.add(ADMIN_PHONE)
    
    # send_webhook fixture already includes valid signature and asserts 200
    send_webhook(text_message_payload(unique_phone, "Hello"))
    
    # Verify the message was processed (should trigger language choice)
    def _has_language_buttons(outbox):
        for btn in outbox.get("buttons", []):
            if btn.get("to", "").lstrip("+") == unique_phone.lstrip("+"):
                button_ids = [b["id"] for b in btn.get("buttons", [])]
                if any(bid.startswith("lang_") for bid in button_ids):
                    return btn
        return None

    lang_msg = poll_outbox(unique_phone, check=_has_language_buttons)
    assert isinstance(lang_msg, dict), f"Expected language buttons for {unique_phone}"
