# Issue #169: created account selection

Baseline `a20d50aefa98b39c996ec07a49efc5bce51ca1e1`; branch `codex/general-issue169-created-account-context-20261003`.

Account creation retains the returned account view ID and passes it to the existing tree reload as an optional preferred selection. Successful reload selects that returned account alongside the refreshed nodes. Ordinary loads/saves retain their existing selected account. The parent-account creation selector remains independent and unchanged. Failed creation or tree reload preserves the previous selection and displays the actual error.

Associated key UI is scoped to the selected account: switching chosen account clears its key-name draft, key list is cleared during the next load, and an effect cleanup discards late key-list success/error from an older account. Default key scopes also respect the actor intersection already used by the visible scope controls. Same-account reload preserves the key name. Creation does not issue a key; only the explicit existing key button does so. No inheritance, grant policy, stale-grant PATCH behavior (#135), backend authority or permission changes.

## Validation

- `uv run pytest`: 429 passed, 19 existing warnings, 14.16 seconds.
- `uv run ruff check xeonolab tests`: passed.
- `uv run python scripts/export_openapi.py`: passed; schema unchanged.
- Frontend `npm test`: 62 passed, zero skipped.
- Frontend `npm run build`: TypeScript/Vite passed.
- `CHROME_BIN=/usr/bin/google-chrome node scripts/verification/created-account-context/verify.mjs`: PASS, eight browser checks and zero page errors.
- `git diff --check`: passed.

Chrome renders the actual Accounts component with a local synthetic in-memory identity/account/key API. It creates a child with desktop/read/query effective scopes, verifies automatic tree/detail selection and the unchanged parent selector, clears a parent key draft, and tests a controlled Promise barrier: the old parent's key read has started but not settled before child creation, then is released after the child context is selected and cannot replace the child's empty key list. No key POST occurs during creation. Creation starts with the actor holding every requested grant, and the fixture validates requested grants against the actor/parent intersection. Immediately after the successful create response, the fixture explicitly shrinks actor request scopes before the normal `/users/me` reload. The child retains its desktop/read/query effective scopes. A subsequent explicit manual key POST uses `synthetic-child-1` and only desktop/read scopes; query is absent from the key controls because the refreshed actor no longer holds it. Later failure scenarios submit only the remaining valid grant intersection. This models a legal identity refresh; no successful request grants scopes the actor lacked at creation time. Ordinary account save/reload retains selection and a same-context key-name draft. Creation rejection and refresh failure both keep the prior account with the exact synthetic error text.

Evidence: `runtime/verification/issue169-created-account-context/browser-report.json`, `created-account-selected.png`, `account-context.png` and gate logs. The success screenshot was visually inspected for the selected child, effective permissions and key form; the final screenshot records refresh-failure feedback. Tests use public local placeholder password/token values, do not retain password/token fields in call reports, and do not copy or save returned token material in evidence. No real credentials or identities were read.

## Operations

No API/schema, dependency or migration changes. No production identity service, account or key mutation occurred. This does not verify production Keycloak/account-authority availability, login or storage; the existing authority remains a production dependency. No production deployment was performed. Rollback: revert the preferred-selection and key-context UI changes plus verification/document files; existing backend objects remain unchanged. Independent of pending Library/desktop-widget PRs.
