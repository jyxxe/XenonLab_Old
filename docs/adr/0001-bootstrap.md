# ADR 0001 — Incremental V1 bootstrap

The source repository starts empty. Use one Python package with independently selectable FastAPI
service factories; each service runs in its own process and data directory. SQLite `.xl` files are
transactional facts, not connection descriptors. The initial catalogue uses SQLite as a documented
single-node bootstrap replacement for PostgreSQL; production PostgreSQL, Qdrant and real model
workers remain required for the full plan. No fabricated embeddings or successful deployments.
OIDC JWTs are checked against issuer, audience and signing key. Development tokens require an
explicit development mode. Production execution remains disabled until an executor is installed.
All release leases are persistent and serialized; expiry does not release an active deployment.
