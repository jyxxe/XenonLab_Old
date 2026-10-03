# Issue 216 — exact int64 editing

The XeLibrary API already accepts signed 64-bit JSON integers. The browser used
ordinary JSON parsing and `Number(raw)`, so `9007199254740993` became
`9007199254740992` before display; saving a different field sent that rounded
value back. This change preserves the existing API protocol and backend ACL,
revision and type checks.

Library responses (`tables`, `query`, `search`, `theorems`) use the shared
`parseAPIJSON` boundary in production `main.tsx`. Safe integer and finite
floating/exponent tokens remain JavaScript numbers; unsafe integer tokens use
BigInt internally. Parsing validates the original JSON grammar and protects
quoted/escaped strings, nested objects, prototype keys and internal marker
collisions. Serialization sends BigInt as unquoted JSON integer tokens. No
`eval`, backend string coercion, new package or production service is involved.

The int64 editor retains decimal text until submission and validates the full
range `-9223372036854775808` to `9223372036854775807`. Invalid fractions,
exponents, out-of-range or already-rounded numeric values fail visibly before
mutation. Schema defaults use the same validation. Existing row updates send
only changed fields as an additional safeguard. Table rendering, numeric sort,
filtering, clipboard, history comparison, query previews and selection context
also handle exact integers and nested JSON without rounding or BigInt errors.

Malformed/non-finite successful Library responses produce a visible
`INVALID_RESPONSE` with the response request ID and allow manual retry. They do
not enter the session's automatic retry queue. HTTP error envelopes and session
generation checks retain their existing order. Other applications retain their
JSON representation and retry behavior. Search filter input parsing is unchanged;
the separate typed-filter PR 164 / Issue 159 is not included or assumed merged.

## Reproduce

```sh
uv run pytest --basetemp runtime/verification/issue216/pytest-tmp
uv run python scripts/export_openapi.py
uv run ruff check scripts/export_openapi.py apps/desktop-web/tests/int64-local-api.py
cd apps/desktop-web
npm test
npm run build
node --test tests/browser-acceptance-int64.test.mjs
npm run test:browser-regression
```

The browser test uses actual production `main.tsx`, Chromium and a real
XeLibrary FastAPI service with an isolated temporary directory. A virtual
`library.xenon.lan` hostname selects the standalone Library view; every physical
request is routed to loopback. Auth-session and users/me endpoints return a
synthetic development owner/admin identity matching the local API's configured
test token. This is not production OIDC, TLS or a real account. Tables, schema,
rows, history and query requests go through the real API; no production data or
permissions are modified. Temporary records are removed when the process exits.

The frontend CI job installs the same pinned uv/Python runtime as the backend
job and synchronizes the locked repository dependencies before browser tests.
The real API fixture requires that runtime even though the application build
itself is JavaScript. Missing executables fail with a visible startup error;
cleanup only waits for a process that actually started. The first remote run
failed in Node's native callback handling before the new browser test could
reach the API, while the other ten browser tests passed. A local PATH excluding
uv reproduced `spawn uv ENOENT`; the fixture now handles this failure explicitly.
That controlled reproduction supports the missing-runtime diagnosis without
claiming to reproduce the exact native assertion from the CI Node version.

Ordinary clicks verify unchanged unsafe integers of both signs and signed64
bounds during a title edit, direct full-range edits, safe integer/float editing,
invalid inputs with zero mutation requests, nested JSON and strings, history,
query display, creation using exact defaults, default editing and new-table
default creation. A malformed HTTP 200 response verifies visible error,
correlation ID and manual recovery. Raw request/readback values prove int64 is
still a JSON number. Evidence and screenshots are saved under
`runtime/verification/issue216/`.

No API/data migration or new production dependency is required. Existing API
clients keep their wire format. Reverting restores the previous UI behavior;
exact persisted integers remain valid in the backend. This change does not
infer or repair values rounded by older clients; any such data requires review
against its historical revisions or original source. Production deployment,
OIDC and full-plan worker dependencies remain outside this fix.
