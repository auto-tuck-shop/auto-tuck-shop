"""Unit tests for mock WhatsApp client typing and read receipts."""

import asyncio

from services.whatsapp.mock_client import MockWhatsAppClient


def test_mock_mark_as_read_and_typing_indicator():
    MockWhatsAppClient.reset()

    client = MockWhatsAppClient()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # mark as read
    res = loop.run_until_complete(client.mark_as_read("wamid.mock_123"))
    assert res is True

    # typing on
    res = loop.run_until_complete(client.send_typing_indicator("+27721234567", "typing_on"))
    assert res is True

    # typing off
    res = loop.run_until_complete(client.send_typing_indicator("+27721234567", "typing_off"))
    assert res is True

    # timeline should include actions
    types = [t["type"] for t in MockWhatsAppClient.timeline]
    assert "mark_as_read" in types
    assert any(t.get("type") == "typing" for t in MockWhatsAppClient.timeline)
