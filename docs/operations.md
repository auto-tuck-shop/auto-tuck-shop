# Operations

This document captures how to run, test, deploy, and support the project during
the pilot.

## Environments

- Local development: Django app, usually backed by local SQLite or Postgres.
- Staging: deployed Fly.io app using mock WhatsApp sending and test endpoints.
- Production: deployed Fly.io app connected to real WhatsApp delivery.

## Deployment Rule

Always deploy to staging first, run tests, then ask a human before production:

```bash
fly deploy -c fly.staging.toml
python -m pytest tests/ -x
# human review
fly deploy
```

Production deploys are irreversible enough to require a human checkpoint.

## Staging Test Paths

Staging supports two main test styles:

- Mock WhatsApp UI at `/mock-whatsapp/` for manual workflow testing.
- HTTP integration tests in `tests/` that send webhook-shaped payloads and poll
  the mock outbox.

The mock path should be used before production changes to confirm:

- new user waitlist flow,
- admin approval,
- text sale recording,
- voice sale recording,
- confirm/fix button behavior.

## Required Configuration Areas

Check `.env.example` for the current variable list. The important production
categories are:

- Django secret, allowed hosts, CSRF origins.
- Database URL.
- Meta WhatsApp access token, phone number ID, verify token, and app secret.
- Admin notification phone.
- OpenRouter API key/model.
- ElevenLabs API key.
- R2 credentials and bucket/public URL.
- Sentry settings.

## Pilot Monitoring

During the pilot, operators should regularly inspect:

- Django admin sales list.
- WhatsApp message history.
- Product catalog and price history.
- Waitlist entries.
- `/admin/pilot-metrics/`.
- Sentry errors.

Useful questions:

- Are sales being recorded for each active shop?
- Are users flagging many bot mistakes?
- Are voice notes failing more often than text?
- Are product names being created incorrectly?
- Are messages delayed, duplicated, or missing?

## Support Playbook

When a shop reports a problem:

1. Find the user by phone number in `UserProfile`.
2. Check recent `WhatsAppMessage` records for the phone/company.
3. Check whether a `Sale` was created.
4. If the user flagged a bot mistake, inspect the sale items and original text or
   transcription.
5. If the issue affects the main pilot flow, reproduce it in staging before
   changing code when possible.

## Migration Rule

Migration files are append-only history. Do not edit or delete committed
migrations. Create a new migration for schema changes and make sure it is
committed with the code that needs it.

## Documentation Rule

When operational behavior changes, update this file or the pilot plan in the
same pull request. Future contributors should not need private context to safely
operate the pilot.
