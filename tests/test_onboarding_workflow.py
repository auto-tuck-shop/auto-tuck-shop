"""Test onboarding: unknown user → waitlist → admin approve → welcome."""

from tests.conftest import text_message_payload


def test_unknown_user_gets_waitlisted(send_webhook, poll_outbox, unique_phone):
    """Unknown user's first message puts them on the waitlist."""
    send_webhook(text_message_payload(unique_phone, "Hello I want to register"))

    def _has_waitlist_msg(outbox):
        for m in outbox.get("messages", []):
            if "waitlist" in m["text"].lower():
                return True
        return None

    result = poll_outbox(unique_phone, check=_has_waitlist_msg)
    assert result is True, f"Expected waitlist message for {unique_phone}. Outbox: {result}"


def test_full_onboarding(send_webhook, poll_outbox, onboard_user, unique_phone):
    """Full flow: register → admin approve → user gets welcome message."""
    onboard_user(unique_phone)

    outbox = poll_outbox(
        unique_phone,
        check=lambda ob: any(
            "welcome" in m["text"].lower() or "approved" in m["text"].lower()
            for m in ob.get("messages", [])
        ) or None,
    )
    assert outbox is True
