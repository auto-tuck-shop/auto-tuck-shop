"""Test assistant management: owner adds an assistant."""

import random

from tests.conftest import text_message_payload


def test_add_assistant(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Owner sends 'add assistant <phone>' and gets confirmation."""
    onboard_user(unique_phone)

    assistant_phone = "+2783" + "".join(random.choices("0123456789", k=7))
    send_webhook(text_message_payload(unique_phone, f"add assistant {assistant_phone}"))

    def _has_assistant_response(outbox):
        for m in outbox.get("messages", []):
            text = m["text"].lower()
            if "added" in text or "assistant" in text:
                return True
        return None

    result = poll_outbox(unique_phone, check=_has_assistant_response, timeout=20.0)
    assert result is True, f"Expected assistant response for {unique_phone}. Outbox: {result}"
