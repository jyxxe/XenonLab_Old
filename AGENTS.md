# Working rules
Read plan.md and relevant ADRs. Never commit credentials. Keep agents behind API boundaries.
Use a feature branch, run `uv run pytest` and regenerate OpenAPI with
`uv run python scripts/export_openapi.py`. Report unimplemented production dependencies honestly.
