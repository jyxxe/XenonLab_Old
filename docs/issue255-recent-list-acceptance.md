# Sidebar Recent: expand beyond the default six (#255)

The sidebar "最近" count already reports the number of saved Recent entries, but
the list rendered only `recent.slice(0, 6)`: entries beyond the newest six were
reachable only by reopening the table through the path tree or search. This
change adds an explicit control instead of silently hiding them.

- `recentListDisplay(recent, showAll)` in `library/logic.js` returns the visible
  entries (six by default), whether an expand/collapse control is needed, the
  hidden count and the control label (`显示全部 N 项` / `收起到 6 项`). It consumes
  only the in-memory Recent list — no new request, pagination, storage cap,
  permission or claim about server-side complete history.
- The sidebar renders the control only when more than six saved entries exist and
  toggles the remaining entries with `aria-expanded`; six or fewer keep the
  previous layout without an extra control.
- No API, persistence or routing change.

## Validation

```sh
cd apps/desktop-web
npm test                                          # includes library-recent-list unit tests
npm run build
CHROME_BIN=... node --test tests/browser-acceptance-recent-list.test.mjs
CHROME_BIN=... npm run test:browser-regression
```

The browser acceptance seeds eight synthetic Recent entries for the fixture
identity, asserts the count and the six-item default, expands to all eight and
collapses back to six, checks `aria-expanded`, and verifies the interaction is
read-only (no non-GET fixture calls, no page errors, no external requests).
