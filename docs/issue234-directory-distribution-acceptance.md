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
six directories, zero tables, a1000-table truncated sample, arbitrary names
and genuine Other directories, plus long shared-prefix names in a360px
viewport. It checks every complete hover/accessible name and exact count,
aggregate count conservation, bar height ratios, no horizontal overflow,
unchanged request paths, and no navigation from passive chart interaction.
Data and readiness are synthetic browser-local fixtures; this is not a
production API or scale test. Backend pytest and OpenAPI export are required
repository checks despite this frontend-only change.
