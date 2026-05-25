"""Test assistant management: owner adds an assistant."""

import random

from tests.conftest import text_message_payload


def test_add_assistant(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Owner sends 'add assistant <phone>' and gets confirmation with the phone number."""
    onboard_user(unique_phone)

    assistant_phone = "+2783" + "".join(random.choices("0123456789", k=7))
    send_webhook(text_message_payload(unique_phone, f"add assistant {assistant_phone}"))

    def _find_assistant_response(outbox):
        for m in outbox.get("messages", []):
            # Look for the response that mentions the assistant's phone number
            if assistant_phone in m["text"]:
                return m
        return None

    msg = poll_outbox(unique_phone, check=_find_assistant_response, timeout=10.0)
    assert isinstance(msg, dict), (
        f"Expected assistant response for {unique_phone}. Outbox: {msg}"
    )

    # Response should echo back the assistant's phone number
    assert assistant_phone in msg["text"], (
        f"Expected assistant phone {assistant_phone} in response: {msg['text']}"
    )
