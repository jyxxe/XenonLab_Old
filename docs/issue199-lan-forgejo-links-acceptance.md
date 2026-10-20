# Issue #199 — consistent desktop Forgejo links

Work list and Dashboard active-task links now use the existing shared
`forgejoIssueUrl` resolver. Public desktop hostname `xenon.jyxxe.com` retains
`https://xenon.jyxxe.com/dev`; LAN IP/hostnames retain
`https://192.168.0.101:9445`. Repository owner/name and positive safe integer
Issue validation are shared with the task widget. Invalid metadata stays plain
text, and valid links keep `_blank` with `noopener noreferrer`.

## Existing implementation dependency

This change builds on the existing, still-open PR #165 implementation for
Issue #162 (`2e9c3f74f0228077d70b3c82ea8021dacf2d469d`). The coordinator
cherry-picked it onto `2c3481d` as `cca1d67`, then rebased this branch onto
`f705776` after the maintainer merged PR #203. Its dependency commit is now
`8e8706e`. That dependency
already provides the lightweight resolver, WorkWidget links, and Issues/apps
integration. This Issue adds only the Work/Dashboard consumers and origin
regression coverage; it does not separately claim or reimplement #162.

PR #165 remains open and its independent CI/preflight is pending. The coordinator
must select the integration path before merging these branches, avoiding two
merges of the same dependency. This document is not evidence of a merge or
production deployment.

## Reproduce

```bash
cd apps/desktop-web
npm test
npm run build
npm run test:browser-regression
```

`tests/browser-acceptance-forgejo-origins.test.mjs` serves the real Work,
Dashboard and WorkWidget components with synthetic APIs. Chromium uses both
public and LAN IP document hostnames; every resource is fulfilled from loopback
Vite and every Issue popup navigation is intercepted locally. Ordinary pointer
clicks assert all six exact URLs, null popup opener, and invalid-metadata
fallback. Unit cases also cover the LAN DNS hostname rule.

The browser test saves six screenshots and `browser-report.json` under
`runtime/verification/issue199-forgejo-origins/`. Production desktop/Forgejo
accounts, authentication, DNS, TLS, connectivity and service availability are
not exercised. No backend endpoint, scope, dependency or data migration changes.
Rollback by restoring the previous frontend bundle or reverting the #199
increment; handle the PR #165 dependency according to the chosen integration.

Coordinator verification on `f705776`: 479 pytest tests, 66 frontend unit
tests, TypeScript/Vite build, all 10 browser regression tests with zero skips,
Ruff for `xeonolab`, `tests` and the OpenAPI export script, and regenerated
OpenAPI with no tracked changes. No production operations were performed.
