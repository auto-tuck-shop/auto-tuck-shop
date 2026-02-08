# Project Guidelines

## Deployment

Always deploy to staging first, run tests, then deploy to prod:

```bash
fly deploy -c fly.staging.toml
python -m pytest tests/ -x
fly deploy
```

## Migrations

Migration files form an immutable, append-only history. Once a migration has been committed and applied, it must never be edited or deleted. When making schema changes:

- Always create a new migration file — never modify an existing one.
- Commit migration files immediately so they're tracked in version control.
- Double-check that new migrations are included in your commit before pushing.
