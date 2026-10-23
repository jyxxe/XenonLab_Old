# Issue #143: copy selected Table View records as TSV

The toolbar copy action uses the intersection of selection and the currently
filtered, sorted, loaded table rows. It includes all selected rows in that view,
including virtualized rows outside the current scroll viewport, and never fetches
an unloaded page. Its count describes the rows that will actually be copied; the
selection badge retains the full selection count. The boundary is shown explicitly.

Columns follow the displayed table order: Row ID, 更新时间, schema columns, 状态.
The timestamp is the original RFC3339 text, preserving its value rather than a
relative date label. Status uses the displayed deleted/attachment/revision labels.
Null and missing values produce empty cells; other scalars preserve their text;
objects/arrays use JSON. Cells containing tab, CR/LF or quotes are enclosed in double
quotes with embedded quotes doubled, using the conventional quoted TSV dialect.

Success gives a row-count confirmation. Clipboard unavailability or rejection shows
a retryable error and keeps the selection. No new API, authorization, data write,
production dependency or database migration is involved.

Local validation on baseline `070b05f`:

- `uv run pytest`: 429 passed, 19 existing temporary-directory cleanup warnings.
- `cd apps/desktop-web && npm test`: 65 passed, including 3 TSV format tests.
- `cd apps/desktop-web && npm run build`: TypeScript and Vite passed.
- `uv run ruff check xeonolab tests scripts/export_openapi.py`: passed.
- `uv run python scripts/export_openapi.py`: passed; OpenAPI unchanged.
- `git diff --check`: passed.
- `cd apps/desktop-web && UI_SCREENSHOT_DIR=/tmp/xeonolab-issue143-screenshots node --test tests/browser-acceptance-library.test.mjs`: 1 passed, 0 skipped, 7.06s; real React/Vite/Google Chrome and synthetic backend data. Covers single/multiple selections, display order, null/JSON/tab/CRLF/quote values, sort/filter, disabled empty visible selection, unloaded-page exclusion, zero new API calls, clipboard rejection/unavailability, retained selection and retry.

The initial failure/retry cases record writes through a page mock. A supplemental
round-trip restores Chrome's native Navigator clipboard getter (`delete
navigator.clipboard`), grants `clipboardReadWrite` and `clipboardSanitizedWrite` only
to the loopback fixture origin using CDP `Browser.grantPermissions`, and brings the
page to the foreground. The actual Table View button writes through native
`navigator.clipboard.writeText`; native `navigator.clipboard.readText` returns the
same header and two complete records, including quoted tab/CR/LF/JSON cells. The
supplemental real Chrome suite passed (1 test, 0 skips, 7.16s).

Persistent evidence: `runtime/verification/issue143-native-clipboard/native-clipboard.json`
and `issue143-native-clipboard.png` in that directory. This validates the native
browser Clipboard API round-trip, not OS application or spreadsheet paste behavior.
No external spreadsheet application was installed or tested. Screenshots `issue143-copy-success.png` and
`issue143-clipboard-failure.png` are in the above temporary directory; the success
screenshot was inspected for accurate copied count and retained selection.

Rollback: revert the frontend PR and rebuild its artifact. No data rollback steps.
This is local acceptance; no production activation is claimed.

Value representation review: the baseline export endpoint returns an `.xl` snapshot;
there is no existing CSV/formula-neutralization policy in the checked baseline. TSV
preserves current authorized client values and applies delimiter quoting without
changing numeric strings, formulas, signs or other scalar text. Delimiter quoting
is a format rule and does not force a destination spreadsheet's cell type; such
applications may interpret formula-looking text according to their own paste rules.
No new data values were introduced for native Clipboard testing.
