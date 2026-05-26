"""Staging integration test: verify typing indicator and read receipts are sent.

This test uses the staging test endpoints and requires .env.staging with
`STAGING_URL`, `TEST_API_KEY`, and `META_WHATSAPP_APP_SECRET` configured.
"""

import json
import pytest

from tests.conftest import text_message_payload


def test_typing_and_mark_as_read(http_client, staging_url, api_key, app_secret, used_phones, unique_phone, send_webhook, poll_outbox):
    """Send a webhook and assert timeline contains typing and mark_as_read entries."""
    used_phones.add(unique_phone)

    # Send a valid webhook (send_webhook signs with app_secret)
    send_webhook(text_message_payload(unique_phone, "Hello for typing test"))

    # Poll /test/outbox for timeline entries
    def _has_timeline(outbox):
        timeline = outbox.get("timeline", [])
        has_typing = any(t.get("type") == "typing" for t in timeline)
        has_read = any(t.get("type") == "mark_as_read" for t in timeline)
        return has_typing and has_read

    result = poll_outbox(unique_phone, check=_has_timeline, timeout=20.0)
    assert result is True, f"Expected typing and mark_as_read in timeline for {unique_phone}. Last outbox: {result}"
