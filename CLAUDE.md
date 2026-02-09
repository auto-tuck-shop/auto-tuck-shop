# Project Guidelines

## Deployment

Always deploy to staging first, run tests, then ask if we are ready to deploy to prod:

```bash
fly deploy -c fly.staging.toml
python -m pytest tests/ -x
# check with human
fly deploy
```

## Migrations

Migration files form an immutable, append-only history. Once a migration has been committed and applied, it must never be edited or deleted. When making schema changes:

- Always create a new migration file — never modify an existing one.
- Commit migration files immediately so they're tracked in version control.
- Double-check that new migrations are included in your commit before pushing.

## Debugging Sentry Issues

When investigating Sentry errors, write a staging integration test that reproduces the failure before fixing. If the bug can't be reproduced in staging (e.g. production-only conditions), recommend improved logging instead.

## Human review requirements

Please stop and check if you're about to do something irreversible (any deployment to prod). Provide evidence for why you're confident in the change including testing in staging
