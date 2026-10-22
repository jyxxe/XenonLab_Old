# Dashboard directory distribution (#234)

The knowledge overview summarizes only Dashboard's currently loaded tables.
It counts the first nonempty path component, preserving `/` for tables at the
root. Categories sort by table count descending, then full name in a stable
lexical order. A Map supports names such as `__proto__` and `constructor`.

The six largest directories each get a numbered bar. With more than six,
one additional neutral bar summarizes every remaining directory. Its legend
states the remaining directory count and exact table count; its hover and
accessible name include all remaining full names and per-directory counts.
Six or fewer directories have no aggregate. Real directories named `Other`,
`其他` or `其他（汇总）` remain ordinary numbered directories, labelled `目录：…`;
the aggregate is explicitly marked as a summary.

Each bar's hover and accessible image name contains the full directory name
and exact table count. The numbered full-name legend wraps without ellipses,
so names sharing a prefix remain distinguishable without hovering. Bars fill
the chart height and encode relative table counts. The existing truncation
notice and `1000+` badge remain when `/tables?limit=1000` returns next_cursor;
the summary does not estimate directories beyond that loaded sample.

No endpoint, permission, refresh cadence, persistence or navigation changes.
The existing knowledge/work buttons retain their behavior. No production
service dependency or database migration is introduced.

Validation in `apps/desktop-web`:

```sh
npm test
npm run build
CHROME_BIN=/usr/bin/google-chrome node --test tests/browser-acceptance-directory-distribution.test.mjs
```

The actual Dashboard Chrome fixture covers eight equal-sized directories,
six directories, zero tables, a 1000-table truncated sample, arbitrary names
and genuine Other directories, plus long shared-prefix names in a360px
viewport. It checks every complete hover/accessible name and exact count,
aggregate count conservation, bar height ratios, no horizontal overflow,
unchanged request paths, and no navigation from passive chart interaction.
Data and readiness are synthetic browser-local fixtures; this is not a
production API or scale test. Backend pytest and OpenAPI export are required
repository checks despite this frontend-only change.

## 2026-10-22 final integration onto release base `d78b50e`

The PR was un-drafted after its earlier WIP/CI-failure state and its branch was integrated onto the current release base `d78b50e2b4c1111d96567696084d143d6222e17e`. The only conflict was the browser-regression test list; it was resolved as a union that keeps this change's `browser-acceptance-directory-distribution.test.mjs` and all base tests added since. No product code conflict remained: `Dashboard.tsx`, `MacCharts.tsx`, `directory-distribution.js` and `style.css` auto-merged onto the current base.

Final integration gates on the merged tree:
- `tsc --noEmit`: passed; `npm test`: **116 passed** (3 new directory-distribution unit tests); `npm run build`: passed.
- `CHROME_BIN=... node --test tests/browser-acceptance-directory-distribution.test.mjs`: **8/8 subtests passed, zero page errors** (eight equal-sized directories, six directories, zero tables, 1000-table truncated sample, arbitrary names, genuine `Other` directories, long shared-prefix names at 360px; complete hover/accessible names, aggregate count conservation, bar height ratios, no horizontal overflow, unchanged request paths, no passive navigation).
- CI `verify / backend`, `verify / frontend` and the protected `devagent/preflight` on the exact integration head are the merge gate; the local Chrome run is coordinator evidence with synthetic data only. No endpoint, permission, persistence, navigation or production dependency change.

## 2026-10-22 CI compatibility fixes after integration head `86dc28b`

The first integrated head failed `verify / frontend` in two pre-existing browser tests
that consume the same Dashboard markup. Both failures were test/environment adaptations,
not product regressions:

- `tests/browser-acceptance-shell.test.mjs` asserted the old knowledge chart accessible
  name format (`示例 1 张表`). The chart now exposes the explicit `目录：示例，1 张表`
  format required by this change, so the shell acceptance assertion was updated to the
  new, more precise name. All other shell expectations are unchanged.
- `tests/browser-acceptance-directory-distribution.test.mjs` asserted the long-name
  360px legend cell with a fixed pixel height. The CI job container resolves CJK text
  differently from the operator workstation (the runner image only installs Latin
  fallback fonts), so CJK glyph advance and hence the exact line count are not stable
  between environments. The long fixture now keeps five long CJK-prefixed names plus one
  long ASCII name; the ASCII name proves the wrap/no-truncate mechanism in every font
  environment, while the CJK names keep their full-text equality checks. The test
  measures the text width the long cell actually needs with its own computed font,
  asserts at least two stable CSS line boxes (line-height is fixed at `20px`), no vertical
  clipping and the wrapping CSS properties (`overflow-wrap: anywhere`, `white-space:
  normal`, `text-overflow: clip`). It also logs the measured ASCII and CJK cell metrics
  for CI diagnostics.
