# Project Guidelines

## Keeping this file current

This file is the source of truth for any AI assistant working on this repo. **Keep it up to date as the project evolves** — when issues are closed, when the phase changes, when new conventions are established, or when the team changes. At the end of any session where meaningful progress was made, update the relevant sections here. Don't let it drift.

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

**Pilot active — production is live as of 2026-05-25.** The bot is receiving and replying to real WhatsApp messages on production.

1. ~~Accounts audit — confirm all API keys are in hand (#18)~~ ✓ done
2. ~~Set Fly.io secrets on staging (#19 partial)~~ ✓ done
3. ~~Deploy to staging and verify app boots (#20)~~ ✓ done
4. ~~Register WhatsApp webhook on Meta dashboard (#23)~~ ✓ done — permanent system user token set
5. ~~E2E test checklist (#44)~~ ✓ done — audio testing deferred to real device
6. ~~Pre-production checklist (#76)~~ ✓ done — Meta policy URLs (privacy/terms/data-deletion) set in App Settings → Basic
7. ~~Deploy to production (#24)~~ ✓ done — live 2026-05-25
8. Onboard 10 pilot shops (#25, assigned Bradley)
9. WhatsApp UX polish — blue ticks, typing indicator, profile name/icon (#77, post-deploy)

When an AI assistant is helping with a task, check which issue it maps to and work within that scope. If a task doesn't map to an open issue, check with the user before starting.

**When creating a new GitHub issue, immediately add it to the project — this is required, not optional:**
```bash
gh issue edit <number> --add-project "Auto Tuck Shop — Backlog"
```

## Where to find context

Before starting any task, read the relevant context:

- **Architecture, data model, flows, async model:** wiki — https://github.com/aakitech/auto-tuck-shop/wiki/Architecture
- **Pilot launch sequence and success metrics:** wiki — https://github.com/aakitech/auto-tuck-shop/wiki/Pilot-Launch
- **All external service integrations:** wiki — https://github.com/aakitech/auto-tuck-shop/wiki/Integrations
- **Key service entry points:** `apps/whatsapp/services/` — sale_handler, waitlist_handler, media_handler, message_parser
- **LLM system prompt (tune for parsing improvements):** `services/openrouter/prompts.py`
- **User-facing strings (EN + Shona):** `apps/whatsapp/locales/en.json`, `sn.json`
- **Unit tests:** `python manage.py test unit_tests`
- **Staging integration tests (manual only):** `python -m pytest tests/ -x` (requires `.env.staging`) — not run in CI, see #82

## Scope rule

Every task should be classified before starting:
- **Pilot blocker** — must be done before first live deploy
- **Pilot support** — useful for operating or observing the pilot
- **Post-pilot** — record in backlog, don't implement now

If a change doesn't improve sale recording, pilot onboarding, operator visibility, or production safety, it belongs after Phase 1.

## Shipping flow

1. **Branch off main** — one branch per issue, named after it (e.g. `fix/duplicate-language-prompt`)
2. **Open a PR** when ready — title includes the issue number
3. **Post in GitHub Discussions → Pull Requests** — tag `@dev-mthandabantu` so she sees it
4. **Madrena reviews and approves** on GitHub — branch protection requires at least one approval before merge
5. **Merge** once approved — squash merge, branch gets deleted
6. **Staging auto-deploys** — GitHub Actions deploys to staging on every push to main (no manual step needed)
7. **Production deploy** — Brighton publishes a GitHub Release → GitHub Actions auto-deploys to prod. Never deploy to production without explicit sign-off from Brighton.

Note: first-ever deploy to a new Fly app must be done manually with `fly deploy` — `fly secrets deploy` fails if no machines exist yet.

### How to create a production release (step-by-step)

Releases follow semver patch bumps (v0.1.0 → v0.1.1 → v0.1.2). Only Brighton publishes releases.

**Prerequisites:** branch is merged to main, staging looks healthy.

```bash
# 1. Check what's in main since the last release (replace vX.X.X with the last tag)
git log vX.X.X..origin/main --oneline

# 2. Find the last release tag so you know what to bump to
gh release list --limit 5

# 3. Publish the release — this triggers the prod deploy workflow automatically
gh release create vX.X.X \
  --title "vX.X.X — <short description>" \
  --notes "## What's in this release

- Fix: <description> (#issue)
- Fix: <description> (#issue)"
```

The `deploy-production.yml` workflow triggers on `release: published` — no manual `fly deploy` needed after the first-ever deploy. Monitor the deploy at: https://github.com/aakitech/auto-tuck-shop/actions

## Deployment

`USE_MOCK_WHATSAPP` must **not** be set (or set to `False`) on production — if it's `True` on prod, the bot silently swallows all outbound messages instead of sending them via WhatsApp. Confirm this before every prod deploy.

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
