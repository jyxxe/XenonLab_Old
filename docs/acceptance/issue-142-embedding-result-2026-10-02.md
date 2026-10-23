# Issue #142: Embedding draft and response consistency

Text or model edits clear the displayed vector, returned model label and encoding
error. A monotonic draft version also invalidates pending responses, including edits
back to the original text. Request versions prevent obsolete completion handlers
from changing a newer request's state and discard completion after unmount.

This does not cancel or abort the synchronous service request. Editing during an
encoding request keeps the generate button busy until that request settles. Its stale
result/error is discarded; the user can then submit the edited text again. API,
authorization, model configuration and production routing are unchanged.

Local verification on baseline `f40ad8b`:

- `uv run pytest`: 427 passed, 19 existing shared temporary-directory cleanup warnings.
- `cd apps/desktop-web && npm test`: 62 passed.
- `cd apps/desktop-web && npm run build`: TypeScript and Vite production build passed.
- `uv run ruff check xeonolab tests scripts/export_openapi.py`: passed.
- `uv run python scripts/export_openapi.py`: passed; no OpenAPI diff.
- `git diff --check`: passed.
- `cd apps/desktop-web && UI_SCREENSHOT_DIR=/tmp/xeonolab-issue142-screenshots node --test tests/browser-acceptance-shell.test.mjs`: 1 passed, 0 skipped, 23.9s; real Chrome shell acceptance, no production dependencies or identities. The fixture supplies synthetic ready models and vectors, and a Promise barrier controls request completion without arbitrary network delays.

The browser regression checks completed-result editing, successful generation of the
new input, pending success and 429 responses after editing and editing back, model
switching during a request, completed-error editing, and busy state until settlement.
It also retains the shell's existing queue denial and scope checks. The synthetic
second model is test-only and does not claim production Qwen readiness.

Screenshot evidence was retained locally in `runtime/verification/issue142/issue142-edited-input.png`.
The screenshot was inspected: the edited input is visible and the old vector is absent.
No schema migration or new production dependency. Rollback: revert the frontend PR
and rebuild the desktop artifact. This is local browser acceptance; production
Embedding endpoint availability and actual inference remain separately tracked.
