# Contributing

Read plan.md, AGENTS.md and applicable ADRs. Work in a feature branch; all production
operations go through Deploy Gateway. PR runners receive no production secrets.

Run backend unit/integration/permission tests, service process smoke tests, regenerate OpenAPI,
and build the TypeScript frontend. New production dependencies must be documented in the ADR
and implementation status. Do not substitute fabricated embeddings, job completion, or deployment
results for working integrations. Forgejo remote and PR workflow are pending infrastructure setup.
