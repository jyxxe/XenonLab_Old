# Issue 205 — readable theorem recents

Opening a theorem from the loaded list, search results or detail retry saves its
available title in the existing per-account recent entry. The original theorem
ID still controls navigation. Generic legacy titles (`定理`), missing titles and
duplicate theorem titles show an ID suffix (last eight characters). A suffix
collision uses the complete ID. Rendering does not rewrite old entries or fetch
metadata in the background.

The sidebar keeps the identifier visible while ellipsizing a long title. The
button tooltip and accessible name include both. Recents/favorites storage,
authentication, ACL checks and API contracts retain their existing behavior;
this change is independent of the cloud persistence work in PR 111.

## Reproduce

```sh
cd apps/desktop-web
npm test
npm run build
node --test tests/browser-acceptance-theorem-recents.test.mjs
npm run test:browser-regression
```

The browser test launches real Chromium against a loopback Vite server and the
existing `library-fixture.html`. All theorem metadata, identity (`recent-*`
subjects), permissions and per-account local records are synthetic. The fixture
uses the existing `{data, meta}` API envelope and `XL_FORBIDDEN` error. No new API
route or production dependency is required. No production user or permissions
are changed, and external requests are blocked and asserted absent.

Acceptance covers two real fixture titles, refresh persistence, long duplicate
titles with visibly distinct IDs, legacy/missing API title fallback, original-ID
ordinary clicks and Enter/Space keyboard navigation, search metadata and retry
after a relation read denial. Pure tests cover short-ID collisions, whitespace,
missing titles, non-theorem entries and unchanged stored objects. Screenshots
and the browser check report are saved to `runtime/verification/issue205/`.
Historical seeded records with UUIDs also verify an eight-character suffix and
two colliding complete UUID identifiers remain visible inside the sidebar.
These records verify rendering only; they do not claim backend existence/access.

No backend migration is needed. Existing legacy entries remain valid. Reverting
the commit restores the earlier presentation while retaining saved recent IDs
and titles. This validation does not certify production OIDC, deployment,
Library Agent or Lean services; those remain outside this UI change.
