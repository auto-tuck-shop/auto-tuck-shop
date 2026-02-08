# Staging Tests Plan

## Goal
Run pytest tests against the deployed staging app at `https://auto-tuck-shop-staging.fly.dev`, asserting on the WhatsApp messages the app *would have sent*.

## Approach
Staging uses `MockWhatsAppClient` which captures all outbound messages in memory. We expose one endpoint to read outbound messages filtered by phone number. Each test uses a unique phone number so tests can run in parallel.

Tests that need a known user onboard through the real flow: send a message (creates waitlist entry) → read admin outbox to get the approve button → click it → user is now onboarded.

## Current State
- Staging app deployed and healthy on Fly.io
- `MockWhatsAppClient` captures `sent_messages` and `sent_buttons` in memory
- 5 test files exist but use Django's in-process `AsyncClient` — need rewriting
- Dev deps (`pytest` etc.) not installed locally

---

## Step 1: Install dev dependencies

Add `httpx` to `requirements-dev.txt`, then:
```bash
.venv/bin/pip install -r requirements-dev.txt
```

## Step 2: Add staging-only outbox endpoint

Create `apps/testing/` with two endpoints, protected by `X-Test-Api-Key` header.

**`GET /test/outbox/?phone=+27821234567`** — returns messages sent to that phone:
```json
{
  "messages": [{"to": "+27821234567", "text": "Welcome!"}],
  "buttons": [{"to": "+27821234567", "body": "Confirm?", "buttons": [...], "message_id": "wamid.mock_0"}]
}
```
Filters `MockWhatsAppClient.sent_messages` and `sent_buttons` by `to` field.

**`POST /test/mock-media/`** — pre-loads audio for mock WhatsApp downloads:
```json
{"media_id": "media_123", "audio_base64": "...", "mime_type": "audio/ogg"}
```
Sets `MockWhatsAppClient.media_downloads[media_id] = (decoded_bytes, mime_type)`.

**Files to create:**
- `apps/testing/__init__.py`
- `apps/testing/views.py`
- `apps/testing/urls.py`

**Wire up (staging only):**
- `config/settings/staging.py`: add `"apps.testing"` to `INSTALLED_APPS`, set `ENABLE_TEST_API = True`
- `config/urls.py`: include `apps.testing.urls` when `getattr(settings, 'ENABLE_TEST_API', False)`

## Step 3: Set secrets

```bash
fly secrets set TEST_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" --config fly.staging.toml
```

## Step 4: Create `.env.staging`

```env
STAGING_URL=https://auto-tuck-shop-staging.fly.dev
TEST_API_KEY=<same value from step 3>
META_WHATSAPP_APP_SECRET=<from existing fly secrets>
```

Add `.env.staging` to `.gitignore`.

## Step 5: Rewrite test infrastructure

**Replace root `conftest.py`** with HTTP-based fixtures:

- `staging_url` — from `STAGING_URL` env var
- `api_key` — from `TEST_API_KEY` env var
- `sign_payload(body: bytes) -> str` — HMAC-signs with `META_WHATSAPP_APP_SECRET`
- `send_webhook(payload: dict)` — signs + POSTs to `{staging_url}/webhook/whatsapp/`
- `get_outbox(phone: str) -> dict` — GETs `{staging_url}/test/outbox/?phone={phone}` with API key
- `upload_mock_media(media_id, audio_bytes, mime_type)` — POSTs to `{staging_url}/test/mock-media/`
- `unique_phone` — returns a unique phone per test, e.g. `+2782{random 7 digits}`
- `r2_audio` — fetches random audio from prod R2 bucket (reuse existing `tests/utils.py` logic)
- `onboard_user(phone: str)` — helper that runs the full onboarding flow:
  1. Sends text message from `phone` (triggers waitlist entry)
  2. Waits, then reads outbox for admin phone `+14342183470`
  3. Extracts `waitlist_approve_{id}` button ID and `message_id` from admin's buttons
  4. Sends button click webhook to approve
  5. Waits, then verifies approval message in outbox for `phone`

**Delete:** `tests/conftest.py`, `tests/factories.py`
**Keep:** `tests/utils.py` (R2 audio fetch)

**Update `pytest.ini`:** remove `DJANGO_SETTINGS_MODULE` since tests are pure HTTP clients.

## Step 6: Rewrite test files

### Onboarding test (`test_onboarding_workflow.py`)
```python
def test_unknown_user_gets_waitlisted(send_webhook, get_outbox, unique_phone):
    send_webhook(text_message(unique_phone, "Hello I want to register"))
    time.sleep(3)
    outbox = get_outbox(unique_phone)
    assert any("waitlist" in m["text"].lower() for m in outbox["messages"])

def test_full_onboarding(send_webhook, get_outbox, onboard_user, unique_phone):
    onboard_user(unique_phone)
    outbox = get_outbox(unique_phone)
    assert any("welcome" in m["text"].lower() or "approved" in m["text"].lower()
               for m in outbox["messages"])
```

### Text sale test (`test_text_sale_workflow.py`)
```python
def test_text_sale_sends_confirmation(send_webhook, get_outbox, onboard_user, unique_phone):
    onboard_user(unique_phone)
    send_webhook(text_message(unique_phone, "sold 2 cokes $5 each"))
    time.sleep(3)
    outbox = get_outbox(unique_phone)
    assert any(b for b in outbox["buttons"] if "confirm" in str(b["buttons"]).lower())
```

### Audio sale test (`test_audio_sale_workflow.py`)
```python
def test_audio_sale_transcribes_and_confirms(
    send_webhook, get_outbox, onboard_user, unique_phone, upload_mock_media, r2_audio
):
    onboard_user(unique_phone)
    audio_bytes, mime_type = r2_audio()
    media_id = f"media_{unique_phone}"
    upload_mock_media(media_id, audio_bytes, mime_type)
    send_webhook(audio_message(unique_phone, media_id))
    time.sleep(5)
    outbox = get_outbox(unique_phone)
    assert len(outbox["messages"]) + len(outbox["buttons"]) >= 1
```

### Confirmation test (`test_confirmation_workflow.py`)
```python
def test_confirm_sale(send_webhook, get_outbox, onboard_user, unique_phone):
    onboard_user(unique_phone)
    send_webhook(text_message(unique_phone, "sold 3 bread $2 each"))
    time.sleep(3)
    outbox = get_outbox(unique_phone)
    confirm_btn = outbox["buttons"][-1]
    confirm_id = [b for b in confirm_btn["buttons"] if "confirm" in b["id"].lower()][0]["id"]
    send_webhook(button_click(unique_phone, confirm_id, confirm_btn["message_id"]))
    time.sleep(2)
    outbox = get_outbox(unique_phone)
    assert any("confirmed" in m["text"].lower() for m in outbox["messages"])
```

### Assistant test (`test_assistant_workflow.py`)
```python
def test_add_assistant(send_webhook, get_outbox, onboard_user, unique_phone):
    onboard_user(unique_phone)
    assistant_phone = "+2783" + "".join(random.choices("0123456789", k=7))
    send_webhook(text_message(unique_phone, f"add assistant {assistant_phone}"))
    time.sleep(3)
    outbox = get_outbox(unique_phone)
    assert any("added" in m["text"].lower() or "assistant" in m["text"].lower()
               for m in outbox["messages"])
```

## Step 7: Deploy and run

```bash
fly deploy --config fly.staging.toml
.venv/bin/pytest -v
```

## Notes

**Parallel safety:** Each test uses a unique phone number. The outbox is filtered per-phone. No shared state, no reset needed.

**Admin outbox:** The `onboard_user` fixture reads the admin phone's outbox (`+14342183470`) to extract the approve button. Multiple tests may see each other's admin notifications, so the fixture should filter for the button matching the specific phone number being onboarded.

**Timing:** Tests use `time.sleep()` to wait for async processing (LLM calls, transcription). Adjust as needed, or poll the outbox with retries.
