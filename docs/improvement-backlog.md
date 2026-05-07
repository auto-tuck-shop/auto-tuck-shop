# Improvement Backlog

This file records important work that should not be forgotten. It is not a
license to derail the current pilot. Classify and pull items into active work
only when they support the current phase.

## Pilot Blockers / Confirm Before Launch

- Enforce Meta webhook signature verification in production. Missing
  `X-Hub-Signature-256` should not be allowed outside deliberate development or
  staging conditions.
- Add idempotency for inbound WhatsApp message IDs so webhook retries cannot
  create duplicate sales.
- Move hard-coded admin phone usage fully into settings/environment.
- Verify inventory calculations ignore cancelled or bot-mistake sales if stock
  is shown to operators.
- Confirm production Sentry privacy settings are intentional, especially around
  phone numbers, raw payloads, and transcripts.

## Pilot Support

- Convert debug `print()` calls in webhook handling to structured logs.
- Add a clear local development setup path separate from production onboarding.
- Add focused unit tests for sale creation, cancellation, inventory stock,
  waitlist approval, and duplicate webhook handling.
- Add a small runbook for common Meta/OpenRouter/ElevenLabs/R2 failures.
- Improve product matching with aliases or review states if bad catalog entries
  become common during the pilot.

## Post-Pilot Product Work

- Richer correction flow after a bot mistake.
- Better dashboards for shop owners.
- Product alias management.
- Bulk import/export for products.
- Advanced inventory workflows.
- More granular permissions.
- Automated alerts and health checks.
- Additional languages and localized support content.

## How To Use This Backlog

Before starting an item, answer:

- Does it protect sale recording, onboarding, operator visibility, or production
  safety for the current pilot?
- Is there evidence from staging, production, or user feedback?
- Can it be shipped without expanding the product surface unnecessarily?

If the answer is no, leave it here.
