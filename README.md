# 🏪 Auto Tuck Shop

AI-powered WhatsApp assistant for tuckshops in Zimbabwe. Shop owners text or voice message what they sold (like "2 cokes $1 each"), and the bot automatically records sales, tracks inventory, and provides insights.

**New here?** → Check out **[ONBOARDING.md](ONBOARDING.md)** for a complete setup guide.

## Tech Stack

- **Django** - Backend framework
- **WhatsApp Business API** (Meta) - Messaging platform
- **OpenRouter** (Gemini) - AI parsing and conversation
- **ElevenLabs** - Voice-to-text
- **Cloudflare R2** - Media storage
- **Fly.io** - Hosting
- **PostgreSQL** - Database

## Development

### Deploying

```bash
# Staging first, run tests, then production
fly deploy -c fly.staging.toml
python -m pytest tests/ -x
fly deploy
```

### Testing on staging

Staging replaces the real WhatsApp API with a mock that captures all outbound messages. You can test in two ways:

**Manual — Mock WhatsApp UI**

1. Deploy to staging
2. Go to https://auto-tuck-shop-staging.fly.dev/mock-whatsapp/
3. Log in with your Django admin credentials

The UI lets you send text messages, click buttons, and see responses as any phone number. Messages go through the full pipeline (webhook handler, LLM parsing, sale creation) — only WhatsApp delivery is mocked.

**Automated — Integration tests**

```bash
python -m pytest tests/ -x
```

Tests are HTTP clients that send webhook payloads to the staging app and poll the mock outbox for responses. They assert on message content, button IDs, reply threading, and message counts.

Required env vars in `.env.staging`: `STAGING_URL`, `TEST_API_KEY`, `META_WHATSAPP_APP_SECRET`.

Tests clean up their own outbox entries after each run (scoped to the phone numbers they used), so they won't interfere with manual testing.
