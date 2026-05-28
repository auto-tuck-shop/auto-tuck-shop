# Project Guidelines

## Keeping this file current

This file is the source of truth for any AI assistant working on this repo. **Keep it up to date as the project evolves** — when the phase changes, when new conventions are established, or when the team changes. At the end of any session where meaningful progress was made, update the relevant sections here. Don't let it drift.

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

Open items:
- Onboard 20 pilot shops (#25, assigned Bradley)
- Typing indicator broken on prod (wrong Meta API payload) — tracked in #77
- WhatsApp profile name/icon still pending (#77)

When an AI assistant is helping with a task, check which issue it maps to and work within that scope. If a task doesn't map to an open issue, check with the user before starting.

**When creating a new GitHub issue, immediately add it to the project — this is required, not optional:**
```bash
gh issue edit <number> --add-project "Auto Tuck Shop — Backlog"
```

## Where to find context

Before starting any task, read the relevant context:

- **Architecture, data model, flows, async model:** wiki (see below)
- **Pilot launch sequence and success metrics:** wiki (see below)
- **All external service integrations:** wiki (see below)
- **Key service entry points:** `apps/whatsapp/services/` — webhook_handler (main entry), sale_handler, waitlist_handler, business_reports, nudge_service, media_handler, message_parser, report_card, whatsapp_client
- **LLM system prompt (tune for parsing improvements):** `services/openrouter/prompts.py`
- **User-facing strings (EN + Shona):** `apps/whatsapp/locales/en.json`, `sn.json`
- **Unit tests:** `python manage.py test unit_tests`
- **Staging integration tests (manual only):** `python -m pytest tests/ -x` (requires `.env.staging`) — not run in CI, see #82

## Working with the wiki

The wiki is a separate git repo. Always clone it locally rather than fetching URLs — it's faster and you can write back to it.

```bash
# Clone once per session (safe to re-run; git will error if already cloned, just cd in)
git clone https://github.com/aakitech/auto-tuck-shop.wiki.git /tmp/ats-wiki
```

Pages and what they cover:

| File | Content |
|------|---------|
| `Architecture.md` | Django apps, data model, URL structure, message pipeline, flows, async model |
| `Features.md` | User-facing features: sale recording, daily reports, onboarding, queries |
| `Integrations.md` | Meta WhatsApp, OpenRouter, ElevenLabs, R2, Fly.io, Sentry |
| `Deployment.md` | Environments, deploy workflow, env vars, migrations, health check |
| `Pilot-Launch.md` | Pilot success metrics and review cadence |
| `Home.md` | Overview and quick links |

**Reading:** `Read /tmp/ats-wiki/Architecture.md` etc. — no WebFetch needed.

**Writing:** Edit files in `/tmp/ats-wiki`, then:
```bash
cd /tmp/ats-wiki && git add <file> && git commit -m "docs: ..." && git push origin master
```

**When to update:** Any time a feature changes behaviour, update the relevant wiki page in the same PR or immediately after. Don't let it drift.

## Scope rule

Production is live. If a change doesn't directly support the 20-shop pilot or fix a real issue, it belongs in the backlog.

## Keeping the backlog current

When closing out a task, update the GitHub issue and backlog as part of the same session:

- **Close issues** when the fix is merged: `gh issue close <number> --comment "Fixed in #<PR>"`
- **Add new issues** for anything discovered mid-work that's out of scope for the current PR. Add them to the project immediately: `gh issue edit <number> --add-project "Auto Tuck Shop — Backlog"`
- **Update priority labels** if the significance of an open issue changes (e.g. a "nice to have" becomes a production bug)
- **Update CLAUDE.md phase status** when the open items above change

## Shipping flow

1. **Branch off main** — one branch per issue, named after it (e.g. `fix/duplicate-language-prompt`)
2. **Open a PR** when ready — title includes the issue number
3. **Post in GitHub Discussions → Pull Requests** — tag `@dev-mthandabantu` so she sees it
4. **Madrena reviews and approves** on GitHub — branch protection requires at least one approval before merge
5. **Merge** once approved — squash merge, branch gets deleted
6. **Staging auto-deploys** — GitHub Actions deploys to staging on every push to main (no manual step needed)
7. **Production deploy** — Brighton publishes a GitHub Release → GitHub Actions auto-deploys to prod. Never deploy to production without explicit sign-off from Brighton.

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

The `deploy-production.yml` workflow triggers on `release: published` — no manual `fly deploy` needed. Monitor the deploy at: https://github.com/aakitech/auto-tuck-shop/actions

## Deployment

`USE_MOCK_WHATSAPP` must **not** be set (or set to `False`) on production — if it's `True` on prod, the bot silently swallows all outbound messages instead of sending them via WhatsApp. Confirm this before every prod deploy.

## Migrations

Migration files are immutable once committed. Never edit or delete an existing migration. Always create a new one:

```bash
python manage.py makemigrations
python manage.py migrate
```

New migrations must be committed immediately and included in the same PR as the schema change.

## Pilot audit

When Brighton says "audit the pilot", "check shop activity", or similar, run:

```bash
fly ssh console --app auto-tuck-shop -C "python manage.py pilot_audit"
```

Pass `--hours 48` (or any number) for a wider window.

Known test accounts excluded automatically: +27644178150, +27610869293

## Language and parsing

The LLM must handle English, Shona, and code-switching. Key Shona vocabulary:
- `imwe` / `imwe neimwe` = "each" — per-unit price marker
- `maviri` = 2, `matatu` = 3, `mana` = 4, `mashanu` = 5
- `ne` = "and" — joins items
- `mazai` = eggs

When making changes to parsing behavior, add or update regression tests in `unit_tests/test_shona_parsing.py`.
