# Project Guidelines

## Deployment

Always deploy to staging first, run tests, then deploy to prod:

```bash
fly deploy -c fly.staging.toml
python -m pytest tests/ -x
fly deploy
```
