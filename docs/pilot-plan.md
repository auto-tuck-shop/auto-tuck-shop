# Pilot Plan

This document exists to keep the project out of rabbit holes. When in doubt,
optimize for a stable first pilot, not for the fully imagined future product.

## Current Phase

**Phase 1: First live pilot readiness**

The project is preparing to go live with a limited set of pilot shops. The main
success condition is that real shop owners can reliably record sales through
WhatsApp, and the team can monitor and support those recordings.

## Phase 1 Goal

Ship a production-ready WhatsApp sales-recording assistant for the first pilot
group.

The pilot should answer:

- Do shop owners actually use WhatsApp to record sales during real shop hours?
- Are text and voice sale messages parsed accurately enough?
- Which product names, currencies, languages, and wording patterns cause trouble?
- Can operators support shops through admin tools and message history?

## Phase 1 Must-Haves

- WhatsApp webhook receives real Meta messages.
- Unknown users enter a waitlist instead of using the system immediately.
- Admin can approve or reject waitlist users.
- Approved users are attached to a shop/company.
- Text sale messages create sales and sale items.
- Voice sale messages are transcribed and then create sales when parsing succeeds.
- Sale replies include a clear receipt and buttons to confirm or flag a mistake.
- Products and prices are available in admin for review.
- WhatsApp messages are stored for audit/debugging.
- Pilot metrics give operators a basic view of shop activity.
- Staging can be used to test the main workflows before production deploys.

## Phase 1 Nice-To-Haves

These are valuable but should not block the first pilot unless they directly
protect sale capture:

- Better product alias handling.
- Cleaner correction workflows after a bot mistake.
- More detailed revenue and inventory analytics.
- Richer shop dashboards.
- More languages.
- Bulk import/export.
- Advanced role permissions.
- Automated operator alerts.

## Explicitly Out Of Scope For Phase 1

Avoid starting these unless the team intentionally changes the phase goal:

- Full POS replacement.
- Payments, checkout, or customer invoicing.
- Complex inventory procurement workflows.
- Multi-branch enterprise management.
- Polished consumer-facing landing pages.
- Sophisticated forecasting or recommendation engines.
- Large UI rebuilds unrelated to pilot operations.

## Definition Of Live-Ready

Before production pilot launch, the team should be confident that:

- The main text sale workflow works in staging.
- The main voice sale workflow works in staging with representative audio.
- Waitlist approval works end to end.
- Bot mistake/cancellation flow works end to end.
- Admin can inspect companies, users, products, sales, and messages.
- Required production environment variables are set.
- Meta webhook verification and message delivery are configured.
- There is a rollback or mitigation plan if the bot starts misbehaving.

## Scope Control Rule

Every new idea should be classified before implementation:

- **Pilot blocker:** must be fixed before first pilot.
- **Pilot support:** useful for operating or observing the pilot.
- **Post-pilot:** record it in the backlog and keep shipping.

If a task does not improve sale recording, pilot onboarding, operator visibility,
or production safety, it probably belongs after Phase 1.

## Current Product Bet

The current bet is not "AI can run a whole shop." The current bet is:

> A shop owner will record more sales if recording is as easy as sending a
> WhatsApp text or voice note.

Protect that bet.
