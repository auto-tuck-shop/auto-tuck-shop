# Auto Tuck Shop Docs

This folder is the shared project memory for Auto Tuck Shop. Keep it practical,
current, and focused on shipping the pilot safely.

## Start Here

- [Project Brief](project-brief.md) explains what the product is and what problem
  it solves.
- [Pilot Plan](pilot-plan.md) defines the current phase, what must work for the
  first pilot, and what is intentionally out of scope.
- [System Architecture](architecture.md) describes the main message flows and
  service boundaries.
- [System Flows](flows.md) shows Mermaid diagrams for the main workflows and
  data relationships.
- [Operations](operations.md) captures staging, testing, deployment, and support
  habits.
- [Improvement Backlog](improvement-backlog.md) records important hardening work
  without letting it distract from the active pilot phase.

## Documentation Habits

Update docs when one of these changes:

- The pilot scope changes.
- A workflow changes for shop owners, admins, or assistants.
- A new external service, environment variable, deployment step, or test path is
  added.
- A production risk is discovered and deferred.
- A future developer would otherwise need to rediscover the same context.

Docs do not need to be perfect. They need to prevent confusion, repeated debate,
and accidental scope creep.
