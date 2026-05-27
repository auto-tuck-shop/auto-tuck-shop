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

ADMIN_PHONE = "+27641295093"


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
    with httpx.Client(timeout=60) as client:
        yield client


# ---------------------------------------------------------------------------
# Phone tracking & per-test cleanup
# ---------------------------------------------------------------------------

@pytest.fixture
def used_phones():
    """Collects phone numbers used during a test for cleanup."""
    return set()


@pytest.fixture
def unique_phone(used_phones):
    digits = "".join(random.choices("0123456789", k=7))
    phone = f"+2782{digits}"
    used_phones.add(phone)
    return phone


@pytest.fixture(autouse=True)
def cleanup_outbox(request, http_client, staging_url, api_key, used_phones):
    """Clear admin outbox before each test, then clean up all used phones after."""
    # Clear admin outbox, then wait for any in-flight async notifications to drain
    # before the next test starts sending webhooks.
    for _ in range(10):
        http_client.delete(
            f"{staging_url}/test/outbox/",
            params={"phone": ADMIN_PHONE},
            headers={"X-Test-Api-Key": api_key},
        )
        time.sleep(1.0)
        resp = http_client.get(
            f"{staging_url}/test/outbox/",
            params={"phone": ADMIN_PHONE},
            headers={"X-Test-Api-Key": api_key},
        )
        outbox = resp.json()
        if not outbox.get("buttons") and not outbox.get("messages"):
            break
    yield
    for phone in used_phones:
        http_client.delete(
            f"{staging_url}/test/outbox/",
            params={"phone": phone},
            headers={"X-Test-Api-Key": api_key},
        )


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
    timeout: float = 5.0,
    interval: float = 0.3,
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
    def _poll(phone: str, *, check, timeout: float = 5.0, interval: float = 0.3):
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


# Hard-coded R2 keys for deterministic audio tests.
# Each is a real voice note chosen to exercise tricky transcription cases.
TEST_AUDIO_R2_KEYS = [
    "2026/02/05/27641295093/1437335271280111.ogg",  # ZA number, 18KB
    "2026/02/05/27641295093/3518063444999554.ogg",  # ZA number, 44KB long clip
    "2026/02/08/263789398574/1283193456994830.ogg",  # ZW number, 14KB
    "2026/02/08/27644178150/1229463476040703.ogg",   # ZA number, 70KB large clip
]


@pytest.fixture(params=TEST_AUDIO_R2_KEYS)
def r2_audio(request, http_client, staging_url, api_key):
    """Fetch a specific real audio file from the production R2 bucket via staging.

    Parameterized over TEST_AUDIO_R2_KEYS so each audio test runs once per file.
    """
    r2_key = request.param

    def _get():
        resp = http_client.get(
            f"{staging_url}/test/r2-sample-audio/",
            params={"key": r2_key, "bucket": "auto-tuck-shop"},
            headers={"X-Test-Api-Key": api_key},
        )
        assert resp.status_code == 200, (
            f"Failed to fetch R2 audio {r2_key}: {resp.status_code}: {resp.text}"
        )
        content_type = resp.headers.get("content-type", "audio/ogg")
        return resp.content, content_type

    return _get


# ---------------------------------------------------------------------------
# Onboarding helper
# ---------------------------------------------------------------------------

@pytest.fixture
def onboard_user(send_webhook, poll_outbox, http_client, staging_url, api_key, app_secret, used_phones):
    """Onboard a user through the full waitlist → approve flow."""
    def _onboard(phone: str):
        used_phones.add(phone)
        used_phones.add(ADMIN_PHONE)
        # 1. Send first message → triggers language buttons
        send_webhook(text_message_payload(phone, "Hello"))

        # 2. Wait for language buttons and click English
        def _find_lang_buttons(outbox):
            for btn in outbox.get("buttons", []):
                if btn.get("to", "").lstrip("+") == phone.lstrip("+"):
                    ids = [b["id"] for b in btn.get("buttons", [])]
                    if any(i.startswith("lang_en_") for i in ids):
                        return btn
            return None

        lang_msg = _poll_outbox(http_client, staging_url, api_key, phone, check=_find_lang_buttons, timeout=10.0)
        assert isinstance(lang_msg, dict) and "buttons" in lang_msg, f"No language buttons for {phone}. Outbox: {lang_msg}"
        en_button = next(b for b in lang_msg["buttons"] if b["id"].startswith("lang_en_"))
        send_webhook(button_click_payload(phone, en_button["id"], lang_msg["message_id"]))

        # 3. Send shop name → triggers admin notification
        send_webhook(text_message_payload(phone, "Test Shop"))

        # 4. Poll admin outbox for the approve button for this phone
        def _find_approve_button(outbox):
            for btn in outbox.get("buttons", []):
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
                if "welcome" in text or "approved" in text or "yagamuchirwa" in text:
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
