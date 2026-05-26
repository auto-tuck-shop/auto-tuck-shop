# Onboarding

## What this project is

Auto Tuck Shop is a WhatsApp bot that helps tuckshop owners in Zimbabwe record sales by text or voice. An owner sends a message like "2 cokes $1 each", the bot parses it with an LLM, records the sale, and sends a confirmation. Sales can be confirmed or flagged as a bot mistake via buttons.

The codebase is a Django app. Messages arrive via the WhatsApp Business API (Meta), are parsed by Gemini 2.5 Flash via OpenRouter, and voice messages are transcribed by ElevenLabs before parsing.

See the [Architecture wiki page](https://github.com/aakitech/auto-tuck-shop/wiki/Architecture) for the full system design.

## Local setup

**Prerequisites:** Python 3.11+, Git.

**1. Clone and create virtual environment**

```bash
git clone https://github.com/aakitech/auto-tuck-shop.git
cd auto-tuck-shop
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows
# or
source .venv/bin/activate    # Mac/Linux
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

**3. Set up .env**

```bash
cp .env.example .env
```

Fill in `.env` with real values (get these from Brighton):

```env
SECRET_KEY=any-long-random-string-for-local-dev
DEBUG=True
DATABASE_URL=sqlite:///db.sqlite3

META_WHATSAPP_ACCESS_TOKEN=
META_WHATSAPP_PHONE_NUMBER_ID=
META_WHATSAPP_VERIFY_TOKEN=
META_WHATSAPP_APP_SECRET=

OPENROUTER_API_KEY=
ELEVENLABS_API_KEY=

R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_ENDPOINT_URL=
R2_BUCKET_NAME=
R2_PUBLIC_URL=

SENTRY_DSN=  # optional
```

**4. Configure WhatsApp Business Profile**

Before first deployment, set up your WhatsApp Business Account profile on Meta's dashboard so customers see your business name and logo instead of just a phone number:

1. Go to [business.facebook.com](https://business.facebook.com)
2. Select your WhatsApp Business Account
3. Navigate to **Phone Numbers** → your phone number → **Profile**
4. Set **Display Name** (e.g., "Auto Tuck Shop") — this requires Meta approval (24-48 hours)
5. Upload a **Profile Picture** (your business logo, PNG/JPEG, recommended 512×512 px)
6. Save and wait for approval if prompted

Once approved, customers will see your business name in the chat header instead of just the phone number.

**5. Migrate and create superuser**

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Admin panel: `http://localhost:8000/admin`

## Service accounts

You need credentials for these services (Brighton holds the master credentials):

| Service | Purpose | Where |
|---|---|---|
| Meta WhatsApp Business | Send/receive messages | developers.facebook.com |
| OpenRouter | LLM parsing (Gemini 2.5 Flash) | openrouter.ai |
| ElevenLabs | Voice transcription | elevenlabs.io |
| Cloudflare R2 | Media storage | cloudflare.com |
| Fly.io | Hosting | fly.io |
| Sentry | Error tracking | sentry.io |

## Deploying

See the [Deployment wiki page](https://github.com/aakitech/auto-tuck-shop/wiki/Deployment) for full deployment steps.

Short version — always staging first:

```bash
fly deploy -c fly.staging.toml
python -m pytest tests/ -x  # requires .env.staging
fly deploy                   # only after Brighton sign-off
```

## Key files

| File | What it does |
|---|---|
| `apps/whatsapp/views.py` | Webhook entry point — receives Meta callbacks |
| `apps/whatsapp/services/webhook_handler.py` | Shared utilities, public entry points |
| `apps/whatsapp/services/message_lock.py` | Per-user message serialization (prevents race conditions) |
| `apps/whatsapp/services/sale_handler.py` | Sale creation and button processing |
| `apps/whatsapp/services/waitlist_handler.py` | Onboarding, language selection, approval |
| `apps/whatsapp/services/media_handler.py` | Audio download, transcription, R2 upload |
| `apps/whatsapp/services/message_parser.py` | LLM call and result parsing |
| `services/openrouter/prompts.py` | System prompt — tune this for parsing improvements |
| `apps/whatsapp/locales/` | `en.json` and `sn.json` — all user-facing strings |
| `config/settings/` | Dev, staging, and production settings |
| `unit_tests/` | Django unit tests (run with `manage.py test unit_tests`) |
| `tests/` | Staging integration tests (run with `pytest tests/ -x`) |

## Architecture: Per-User Message Queueing

To prevent duplicate sales and race conditions when a shop owner sends multiple messages rapidly, the system implements **per-user message serialization**:

- **One message at a time per user** — messages from the same phone number are processed serially
- **Database row-level lock** — uses Django's `select_for_update()` on the user's profile
- **Automatic duplicate prevention** — `whatsapp_message_id` has a unique constraint at the DB level
- **Different users in parallel** — no global lock; concurrent messages from different users still process in parallel

### When per-user locking happens

1. Message arrives from user A
2. System acquires a lock on user A's profile row
3. Message is parsed, sale is created, LLM response is generated and sent
4. Lock is released
5. Any pending messages from user A now acquire the lock and process (in order)

### Scenarios prevented

- **Network retry:** User sends sale, loses network, Meta retries same webhook → only one record created (DB constraint detects duplicate `whatsapp_message_id`)
- **Rapid messages:** User sends "5 bread" then "3 coke" before first reply → both process in order (per-user lock ensures serialization)
- **Correction before confirmation:** User sends "10 maize" then "5 maize" before bot replies → first is processed, then second (in order, no duplicates)

### See also

- [Deployment wiki](https://github.com/aakitech/auto-tuck-shop/wiki/Deployment) — lock timeout and performance tuning
- `apps/whatsapp/services/message_lock.py` — implementation details and docstrings
- `unit_tests/test_concurrent_message_safety.py` — unit tests for lock behavior



## Admin panel

`/admin/` — key sections:

- **Core > Waitlist Entries** — new users awaiting approval
- **Core > User Profiles** — approved shop owners and assistants
- **Sales > Sales** — all recorded sales
- **Catalog > Products** — product catalog per company

## Common commands

```bash
# Run Django unit tests
python manage.py test unit_tests

# Run staging integration tests (requires STAGING_URL in .env.staging)
python -m pytest tests/ -x

# Check for config errors
python manage.py check

# Apply migrations
python manage.py migrate

# Open shell on production
fly ssh console --app auto-tuck-shop
```

## Updating the admin phone number

The admin phone receives waitlist notifications. It is set in:

`apps/whatsapp/services/webhook_handler.py` — `ADMIN_PHONE_NUMBER`

## Troubleshooting

**WhatsApp messages not received** — check webhook URL in Meta dashboard, check Fly logs (`fly logs --app auto-tuck-shop`), verify `META_WHATSAPP_VERIFY_TOKEN` matches.

**LLM parsing wrong** — check `OPENROUTER_API_KEY`, review `services/openrouter/prompts.py`.

**Voice messages not working** — check ElevenLabs key and quota, verify R2 secrets.

**Migration errors** — never edit old migration files. Create new ones with `python manage.py makemigrations`.

**Fly deployment fails** — check secrets are set (`fly secrets list`), check logs.
