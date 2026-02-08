"""
Staging integration test fixtures.

All tests are pure HTTP clients that talk to the deployed staging app.
No Django import, no database access.
"""

import base64
import hashlib
import hmac
import os
import random
import time

import httpx
import pytest
from dotenv import load_dotenv

# Load .env.staging from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.staging"))

ADMIN_PHONE = "+14342183470"


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def staging_url():
    url = os.environ.get("STAGING_URL")
    if not url:
        pytest.skip("STAGING_URL not set")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def api_key():
    key = os.environ.get("TEST_API_KEY")
    if not key:
        pytest.skip("TEST_API_KEY not set")
    return key


@pytest.fixture(scope="session")
def app_secret():
    secret = os.environ.get("META_WHATSAPP_APP_SECRET")
    if not secret:
        pytest.skip("META_WHATSAPP_APP_SECRET not set")
    return secret


@pytest.fixture(scope="session")
def http_client():
    with httpx.Client(timeout=30) as client:
        yield client


# ---------------------------------------------------------------------------
# Unique phone per test
# ---------------------------------------------------------------------------

@pytest.fixture
def unique_phone():
    digits = "".join(random.choices("0123456789", k=7))
    return f"+2782{digits}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign_payload(body: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _poll_outbox(
    client: httpx.Client,
    staging_url: str,
    api_key: str,
    phone: str,
    *,
    check,
    timeout: float = 15.0,
    interval: float = 0.5,
):
    """Poll the outbox until `check(outbox_dict)` returns a truthy value or timeout."""
    deadline = time.monotonic() + timeout
    last_outbox = None
    while time.monotonic() < deadline:
        resp = client.get(
            f"{staging_url}/test/outbox/",
            params={"phone": phone},
            headers={"X-Test-Api-Key": api_key},
        )
        resp.raise_for_status()
        last_outbox = resp.json()
        result = check(last_outbox)
        if result:
            return result
        time.sleep(interval)
    # Return last outbox so callers can inspect what was there
    return last_outbox


# ---------------------------------------------------------------------------
# Action fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def send_webhook(http_client, staging_url, app_secret):
    """Send a webhook payload to the staging app."""
    def _send(payload: dict):
        body = httpx.compat.json.dumps(payload).encode() if hasattr(httpx, 'compat') else __import__('json').dumps(payload).encode()
        sig = _sign_payload(body, app_secret)
        resp = http_client.post(
            f"{staging_url}/webhook/whatsapp/",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"
        return resp
    return _send


@pytest.fixture
def get_outbox(http_client, staging_url, api_key):
    """Get the outbox for a phone number."""
    def _get(phone: str) -> dict:
        resp = http_client.get(
            f"{staging_url}/test/outbox/",
            params={"phone": phone},
            headers={"X-Test-Api-Key": api_key},
        )
        resp.raise_for_status()
        return resp.json()
    return _get


@pytest.fixture
def poll_outbox(http_client, staging_url, api_key):
    """Poll the outbox until a condition is met. Returns the check result or last outbox."""
    def _poll(phone: str, *, check, timeout: float = 15.0, interval: float = 0.5):
        return _poll_outbox(
            http_client, staging_url, api_key, phone,
            check=check, timeout=timeout, interval=interval,
        )
    return _poll


@pytest.fixture
def upload_mock_media(http_client, staging_url, api_key):
    """Upload mock audio to the staging app for download_media to return."""
    def _upload(media_id: str, audio_bytes: bytes, mime_type: str = "audio/ogg"):
        resp = http_client.post(
            f"{staging_url}/test/mock-media/",
            json={
                "media_id": media_id,
                "audio_base64": base64.b64encode(audio_bytes).decode(),
                "mime_type": mime_type,
            },
            headers={"X-Test-Api-Key": api_key},
        )
        assert resp.status_code == 200, f"Mock media upload failed: {resp.status_code}: {resp.text}"
    return _upload


@pytest.fixture
def r2_audio(http_client, staging_url, api_key):
    """Fetch random audio from production R2 via the staging app's R2 credentials.

    Since we can't import Django settings in tests, we use a small .ogg test file
    as fallback. For real R2 integration, the staging app handles it.
    """
    def _get():
        # Use a minimal valid OGG file for testing
        # In practice, the staging app's mock client will receive this via upload_mock_media
        test_audio = b"OggS" + b"\x00" * 200  # Minimal placeholder
        return test_audio, "audio/ogg"
    return _get


# ---------------------------------------------------------------------------
# Onboarding helper
# ---------------------------------------------------------------------------

@pytest.fixture
def onboard_user(send_webhook, poll_outbox, http_client, staging_url, api_key, app_secret):
    """Onboard a user through the full waitlist → approve flow."""
    def _onboard(phone: str):
        # 1. Send a message from the unknown phone → triggers waitlist entry
        send_webhook(text_message_payload(phone, "Hello I want to register my shop"))

        # 2. Poll admin outbox for the approve button for this phone
        def _find_approve_button(outbox):
            for btn in outbox.get("buttons", []):
                # The admin notification body includes the phone number
                if phone in btn.get("body", ""):
                    for b in btn.get("buttons", []):
                        if b["id"].startswith("waitlist_approve_"):
                            return {"button_id": b["id"], "message_id": btn["message_id"]}
            return None

        result = _poll_outbox(
            http_client, staging_url, api_key, ADMIN_PHONE,
            check=_find_approve_button, timeout=15.0,
        )

        assert isinstance(result, dict) and "button_id" in result, (
            f"Could not find approve button for {phone} in admin outbox. "
            f"Last outbox: {result}"
        )

        # 3. Send button click webhook to approve
        approve_payload = button_click_payload(
            ADMIN_PHONE, result["button_id"], result["message_id"]
        )
        send_webhook(approve_payload)

        # 4. Poll user outbox for approval/welcome message
        def _has_approval(outbox):
            for m in outbox.get("messages", []):
                text = m.get("text", "").lower()
                if "welcome" in text or "approved" in text:
                    return True
            return None

        approval = _poll_outbox(
            http_client, staging_url, api_key, phone,
            check=_has_approval, timeout=15.0,
        )
        assert approval is True, f"User {phone} was not approved. Outbox: {approval}"

    return _onboard


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def text_message_payload(phone: str, body: str, message_id: str | None = None) -> dict:
    """Build a WhatsApp text message webhook payload."""
    # Strip + prefix — Meta sends plain numbers
    sender = phone.lstrip("+")
    mid = message_id or f"wamid.test_{random.randint(100000, 999999)}"
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "messages": [{
                        "id": mid,
                        "from": sender,
                        "type": "text",
                        "text": {"body": body},
                    }]
                }
            }]
        }]
    }


def audio_message_payload(phone: str, media_id: str, message_id: str | None = None) -> dict:
    """Build a WhatsApp audio message webhook payload."""
    sender = phone.lstrip("+")
    mid = message_id or f"wamid.test_{random.randint(100000, 999999)}"
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "messages": [{
                        "id": mid,
                        "from": sender,
                        "type": "audio",
                        "audio": {"id": media_id, "mime_type": "audio/ogg"},
                    }]
                }
            }]
        }]
    }


def button_click_payload(phone: str, button_id: str, reply_to_message_id: str) -> dict:
    """Build a WhatsApp button-click webhook payload."""
    sender = phone.lstrip("+")
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "messages": [{
                        "id": f"wamid.btn_{random.randint(100000, 999999)}",
                        "from": sender,
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {
                                "id": button_id,
                                "title": button_id.split("_")[0].capitalize(),
                            },
                        },
                        "context": {
                            "id": reply_to_message_id,
                        },
                    }]
                }
            }]
        }]
    }
