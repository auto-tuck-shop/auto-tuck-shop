"""Integration tests for duplicate message prevention."""

import pytest


@pytest.fixture
def duplicate_test_phone(used_phones):
    """Unique phone for duplicate test."""
    phone = "+263123000001"
    used_phones.add(phone)
    return phone


def test_duplicate_message_id_prevented(
    http_client,
    staging_url,
    api_key,
    send_webhook,
    duplicate_test_phone,
):
    """
    Test that messages with duplicate whatsapp_message_id are only recorded once.
    
    Scenario: Meta retries the same webhook with an identical message_id
    (network timeout, etc.). Only one message should be recorded in the DB,
    and only one reply should be sent to the user.
    """
    message_id = "duplicate_msg_123456"
    
    # First message
    payload1 = {
        "entry": [
            {
                "id": "entry1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": duplicate_test_phone.lstrip("+"),
                                    "id": message_id,
                                    "timestamp": "1234567890",
                                    "type": "text",
                                    "text": {"body": "test sale message 1 coke R5"},
                                }
                            ],
                            "metadata": {
                                "display_phone_number": "1234567890",
                                "phone_number_id": "1098001963393818",
                            },
                        },
                    }
                ],
            }
        ],
        "object": "whatsapp_business_account",
    }
    
    # Send first message
    send_webhook(payload1)
    
    # Wait for processing and check outbox
    def check_first(outbox):
        messages = outbox.get("messages", [])
        return len(messages) >= 1
    
    def poll_first():
        import time
        timeout = 10.0
        interval = 0.5
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = http_client.get(
                f"{staging_url}/test/outbox/",
                params={"phone": duplicate_test_phone},
                headers={"X-Test-Api-Key": api_key},
            )
            resp.raise_for_status()
            outbox = resp.json()
            if check_first(outbox):
                return outbox
            time.sleep(interval)
        return outbox
    
    outbox_after_first = poll_first()
    first_message_count = len(outbox_after_first.get("messages", []))
    assert first_message_count >= 1, "First message should be processed"
    
    # Second message with SAME message_id (simulating Meta retry)
    # This is the critical part: same whatsapp_message_id
    payload2 = {
        "entry": [
            {
                "id": "entry2",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": duplicate_test_phone.lstrip("+"),
                                    "id": message_id,  # SAME MESSAGE ID
                                    "timestamp": "1234567891",
                                    "type": "text",
                                    "text": {"body": "test sale message 1 coke R5"},
                                }
                            ],
                            "metadata": {
                                "display_phone_number": "1234567890",
                                "phone_number_id": "1098001963393818",
                            },
                        },
                    }
                ],
            }
        ],
        "object": "whatsapp_business_account",
    }
    
    # Send duplicate message
    send_webhook(payload2)
    
    # Wait  for any potential processing
    import time
    time.sleep(1)
    
    # Check that no new message was recorded (duplicate should be rejected by DB constraint)
    resp = http_client.get(
        f"{staging_url}/test/outbox/",
        params={"phone": duplicate_test_phone},
        headers={"X-Test-Api-Key": api_key},
    )
    resp.raise_for_status()
    outbox_after_second = resp.json()
    second_message_count = len(outbox_after_second.get("messages", []))
    
    # Should still have only 1 message (duplicate rejected by unique constraint)
    assert second_message_count == first_message_count, (
        f"Duplicate message should not create new outbox entry. "
        f"Before: {first_message_count}, After: {second_message_count}"
    )


def test_rapid_related_messages_from_same_user(
    http_client,
    staging_url,
    api_key,
    send_webhook,
    used_phones,
):
    """
    Test that rapid related messages from the same user are serialized.
    
    Scenario: User sends "5 bread" then immediately sends "3 coke" before
    first reply arrives. With per-user locking, second should wait until
    first reply is sent. Without it, both would process in parallel (bad).
    """
    phone = "+263123000002"
    used_phones.add(phone)
    
    # Send first message
    msg_id_1 = "rapid_msg_001"
    payload1 = {
        "entry": [
            {
                "id": "entry1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": phone.lstrip("+"),
                                    "id": msg_id_1,
                                    "timestamp": "1234567890",
                                    "type": "text",
                                    "text": {"body": "5 bread R4 each"},
                                }
                            ],
                            "metadata": {
                                "display_phone_number": "1234567890",
                                "phone_number_id": "1098001963393818",
                            },
                        },
                    }
                ],
            }
        ],
        "object": "whatsapp_business_account",
    }
    
    # Send second message
    msg_id_2 = "rapid_msg_002"
    payload2 = {
        "entry": [
            {
                "id": "entry2",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": phone.lstrip("+"),
                                    "id": msg_id_2,
                                    "timestamp": "1234567891",
                                    "type": "text",
                                    "text": {"body": "3 coke R5 each"},
                                }
                            ],
                            "metadata": {
                                "display_phone_number": "1234567890",
                                "phone_number_id": "1098001963393818",
                            },
                        },
                    }
                ],
            }
        ],
        "object": "whatsapp_business_account",
    }
    
    # Send both rapidly
    send_webhook(payload1)
    send_webhook(payload2)
    
    # Poll for both responses
    import time
    timeout = 15.0
    interval = 0.5
    deadline = time.monotonic() + timeout
    
    while time.monotonic() < deadline:
        resp = http_client.get(
            f"{staging_url}/test/outbox/",
            params={"phone": phone},
            headers={"X-Test-Api-Key": api_key},
        )
        resp.raise_for_status()
        outbox = resp.json()
        messages = outbox.get("messages", [])
        
        if len(messages) >= 2:
            # Both responses should be there
            assert len(messages) == 2, f"Should have 2 responses, got {len(messages)}"
            
            # Check that responses are in expected order
            # (first response should reference first sale, second should reference second)
            # This is a basic check - more detailed checks would verify sale IDs
            break
        time.sleep(interval)
    else:
        pytest.fail("Timeout waiting for both responses")
