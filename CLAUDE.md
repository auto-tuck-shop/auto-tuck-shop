# Project Guidelines

## Start of every session

Before doing anything else, ask the user these two questions:

1. **Who is working?** — Brighton or Madrena (this changes how you respond: Brighton gets concise technical responses; Madrena gets more explanation and junior-friendly framing, especially around PR workflow and Git)
2. **What are we working on?** — ask for the GitHub issue number if there is one, or a short description if it's exploratory. Then read that issue before starting.

If the user's first message already makes it obvious (e.g. "fix issue #18"), skip the question it answers and only ask what's still unclear.

---

Auto Tuck Shop is a Django app. Shop owners in Zimbabwe send WhatsApp text or voice messages to record sales. The bot parses them with an LLM (Gemini 2.5 Flash via OpenRouter), creates sale records, and replies with a confirmation. Voice messages are transcribed by ElevenLabs first.

## Team

| Person | Role | GitHub |
|---|---|---|
| Brighton | Tech lead, repo owner | @dev-thandabantu |
| Madrena | Junior developer | @dev-mthandabantu |
| Bradley | Field/business, pilot shop relationships | @bradleychibuwe105-coder |

## Current phase

**Pilot prep — not yet live.** The code is ready. Blocking items before first deploy:

1. Accounts audit — confirm all API keys are in hand (#18, assigned Madrena)
2. Set Fly.io secrets on staging and production (#19)
3. Deploy to staging and verify app boots (#20)
4. E2E test checklist (#44, blocked on #20)
5. Register WhatsApp webhook on Meta dashboard (#23, assigned Madrena)
6. Deploy to production — Brighton sign-off required (#24)
7. Onboard 10 pilot shops (#25, assigned Bradley)

When an AI assistant is helping with a task, check which issue it maps to and work within that scope. If a task doesn't map to an open issue, check with the user before starting.

## Where to find context

Before starting any task, read the relevant context:

- **What the product is and why:** `docs/project-brief.md`
- **Pilot scope and what's in/out of scope:** `docs/pilot-plan.md`
- **System architecture and message flows:** `docs/architecture.md`, `docs/flows.md`
- **Deployment and operations habits:** `docs/operations.md`
- **Backlog of deferred work:** `docs/improvement-backlog.md`
- **Key service entry points:** `apps/whatsapp/services/` — sale_handler, waitlist_handler, media_handler, message_parser
- **LLM system prompt (tune for parsing improvements):** `services/openrouter/prompts.py`
- **User-facing strings (EN + Shona):** `apps/whatsapp/locales/en.json`, `sn.json`
- **Unit tests:** `python manage.py test unit_tests`
- **Staging integration tests:** `python -m pytest tests/ -x` (requires `.env.staging`)

## Scope rule

Every task should be classified before starting:
- **Pilot blocker** — must be done before first live deploy
- **Pilot support** — useful for operating or observing the pilot
- **Post-pilot** — record in backlog, don't implement now

If a change doesn't improve sale recording, pilot onboarding, operator visibility, or production safety, it belongs after Phase 1.

## Deployment

Always deploy to staging first, run tests, then ask Brighton before deploying to production:

```bash
fly deploy -c fly.staging.toml
python -m pytest tests/ -x
# wait for Brighton sign-off
fly deploy
```

Never deploy to production without explicit human confirmation.

## Migrations

Migration files are immutable once committed. Never edit or delete an existing migration. Always create a new one:

```bash
python manage.py makemigrations
python manage.py migrate
```

New migrations must be committed immediately and included in the same PR as the schema change.

## Debugging Sentry issues

Write a staging integration test that reproduces the failure before fixing. If the bug can't be reproduced in staging, recommend improved logging instead.

## Language and parsing

The LLM must handle English, Shona, and code-switching. Key Shona vocabulary:
- `imwe` / `imwe neimwe` = "each" — per-unit price marker
- `maviri` = 2, `matatu` = 3, `mana` = 4, `mashanu` = 5
- `ne` = "and" — joins items
- `mazai` = eggs

When making changes to parsing behavior, add or update regression tests in `unit_tests/test_shona_parsing.py`.
