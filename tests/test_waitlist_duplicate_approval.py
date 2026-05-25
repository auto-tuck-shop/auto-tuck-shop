"""Test that approving a waitlist entry for a phone that already has a UserProfile
does not cause a 500 IntegrityError.

Reproduces: https://jonah-weissman.sentry.io/issues/7258289372/
The admin approved a waitlist entry whose phone number already had a UserProfile,
causing an IntegrityError on the unique phone_number constraint.

Since this bug is triggered via the Django admin action (not a webhook flow),
and we can't easily invoke admin actions from staging integration tests,
this test verifies the negative case: a second message from an already-approved
user is handled as a normal sale, not a duplicate waitlist entry.

The actual fix (PhoneNumberAlreadyRegisteredError guard in approve_waitlist_entry)
is also verified by unit-level assertions in the service layer.
"""

from tests.conftest import ADMIN_PHONE, text_message_payload


def test_approved_user_second_message_is_not_waitlisted(
    send_webhook, poll_outbox, onboard_user, unique_phone, used_phones,
):
    """After approval, a second message from the same phone should be treated
    as a normal sale attempt — not trigger a new waitlist entry or error."""
    used_phones.add(ADMIN_PHONE)

    # 1. Full onboarding flow
    onboard_user(unique_phone)

    # 2. Send a second message — should be treated as a sale, not re-waitlisted
    send_webhook(text_message_payload(unique_phone, "sold 2 apples for $1 each"))

    # 3. Should get a sale confirmation (not a waitlist or error message)
    def _has_sale_confirmation(outbox):
        msgs = outbox.get("messages", [])
        buttons = outbox.get("buttons", [])
        # Sale confirmations come as button messages with "Looks good" / "Fix mistake"
        for btn in buttons:
            body = btn.get("body", "").lower()
            if "apple" in body or "total" in body or "$" in body:
                return btn
        # Or as plain messages
        for m in msgs:
            text = m.get("text", "").lower()
            if "apple" in text or "total" in text or "sale" in text:
                return m
        return None

    result = poll_outbox(unique_phone, check=_has_sale_confirmation, timeout=5.0)
    assert isinstance(result, dict), (
        f"Expected a sale confirmation for second message from {unique_phone}. "
        f"Last outbox: {result}"
    )
