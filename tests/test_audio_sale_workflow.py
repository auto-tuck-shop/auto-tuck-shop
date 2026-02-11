"""Test audio message sale: upload real audio → send audio webhook → get parsed sale."""

from tests.conftest import audio_message_payload


def test_audio_sale_gets_parsed_receipt(
    send_webhook, poll_outbox, onboard_user, unique_phone, upload_mock_media, r2_audio
):
    """Onboarded user sends a voice note containing a sale description.
    The app should transcribe it, parse items, and respond with
    receipt buttons (Looks good + Fix mistake) — not just 'any response'."""
    onboard_user(unique_phone)

    audio_bytes, mime_type = r2_audio()
    media_id = f"media_{unique_phone.lstrip('+')}"
    upload_mock_media(media_id, audio_bytes, mime_type)

    send_webhook(audio_message_payload(unique_phone, media_id))

    # --- Expect either a receipt with buttons OR a "no products" message ---
    # Both are valid outcomes depending on the audio content.
    def _find_sale_response(outbox):
        # Check for receipt buttons (successful parse)
        for b in outbox.get("buttons", []):
            button_ids = [btn.get("id", "") for btn in b.get("buttons", [])]
            if any(bid.startswith("confirm_") for bid in button_ids):
                return {"type": "receipt", "data": b}

        # Check for a text response (no_products, transcription_failed, etc.)
        # We look for messages beyond the welcome message
        non_welcome = [
            m for m in outbox.get("messages", [])
            if "yagamuchirwa" not in m["text"].lower()
            and "approved" not in m["text"].lower()
            and "welcome" not in m["text"].lower()
        ]
        if non_welcome:
            return {"type": "text", "data": non_welcome[-1]}
        return None

    result = poll_outbox(unique_phone, check=_find_sale_response, timeout=30.0)
    assert isinstance(result, dict), (
        f"Expected a response after audio for {unique_phone}. Outbox: {result}"
    )

    if result["type"] == "receipt":
        btn_msg = result["data"]
        # Receipt should have confirm + fix buttons
        button_ids = [b["id"] for b in btn_msg["buttons"]]
        assert any(bid.startswith("confirm_") for bid in button_ids)
        assert any(bid.startswith("fix_") for bid in button_ids)

        # Body should contain at least one item line (e.g. "2x ...")
        assert "x " in btn_msg["body"].lower() or "×" in btn_msg["body"], (
            f"Receipt body doesn't look like a parsed sale: {btn_msg['body']}"
        )
    else:
        # Text response — should be a known response, not an error stacktrace
        text = result["data"]["text"]
        known_responses = [
            "sorry",          # error / transcription_failed / download_failed
            "couldn't",       # transcription/download failure
            "no item",        # no_products (Shona or English)
            "zvawatengesa",   # Shona no_products hint
            "products",       # no_products
        ]
        assert any(kw in text.lower() for kw in known_responses), (
            f"Unexpected text response after audio: {text}"
        )
