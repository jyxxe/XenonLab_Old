# Issue 219 — Library workspace identity isolation

Library resource and interface state now belongs to a workspace keyed by the
verified subject and authentication epoch. An epoch change removes the previous
workspace synchronously while `/users/me` revalidates the next identity. Until
verification succeeds, no resource workspace or anonymous preferences mount.
Identity failures show the error code/request ID and offer manual retry.

Catalogue, table/row/schema, search, query, theorem, selection, Inspector,
dialogs and upload recovery state are discarded together. Subject-owned local
preferences/query drafts still restore for their owner after verification.
The workspace's stable API wrappers check a lifetime generation before and
after each request. Unmount rejects late results and prevents old asynchronous
chains from issuing follow-up requests using a newer session's credentials.
Effect replay cannot make a previous request generation current again. This
does not cancel or roll back a mutation already accepted by the server.

Run from the repository root:

```sh
uv run pytest --basetemp runtime/verification/issue219/pytest-tmp
uv run python scripts/export_openapi.py
uv run ruff check scripts/export_openapi.py apps/desktop-web/tests/session-cache-local-api.py
cd apps/desktop-web
npm test
npm run build
node --test tests/browser-acceptance-session-cache.test.mjs
npm run test:browser-regression
```

The tracked browser regression uses actual LibraryApp components, stable API
references, a real temporary Library API and two fixed synthetic development
identities. B's private-row GET is denied by the real backend ACL; its catalogue
includes only a separately granted table. Ordinary clicks cover A→B→A,
same-subject new epochs, pending/failed identity with manual retry, and late
catalogue, row, keyword-search and theorem bodies. A decoded-response marker
ensures late bodies have actually arrived before asserting no old data or
B-token follow-up requests. Existing query tests retain their anonymous-storage,
unverified execution and late-query assertions.

A second entry renders production main.tsx in a real iframe. A parent fixture's
ordinary button uses native postMessage; the browser supplies origin/source.
Virtual trusted HTTPS origins and every asset/API are intercepted and served
physically by loopback. An open AI draft containing A's fixture value disappears
while B is pending. No MessageEvent constructor supplies fabricated provenance.
This verifies the local browser message contract, not real production identity,
DNS/TLS, OIDC, parent authentication or deployment. Reports and screenshots are
under `runtime/verification/issue219/`.

Frontend CI installs pinned Python/uv and the locked backend environment because
this regression starts a real API. Missing uv produces an explicit test failure;
the regression does not substitute a fake API or skip it. No production package,
service, API contract, ACL rule or data migration changes. Reverting restores
the previous cache retention risk across identity changes. Full-plan production
dependencies remain outside this fix.
