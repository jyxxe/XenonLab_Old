# Local browser QA — 2026-10-01

This regression pass used the current desktop React application in local Vite and Chromium with synthetic API responses. It did not sign into or write to production, so it is not production acceptance.

The browser exercised Knowledge table switching and row pagination, a delayed response arriving after a table switch, history restore with `If-Match`, attachment retry after a simulated 503, ACL/settings views, LaunchPad and Knowledge entry points, and the Work center's Forgejo Issue links. The delayed response did not replace the newly selected table. Restore sent the expected revision and `If-Match`; upload retry allowed selecting the file again. No JavaScript exception or unhandled fixture route was observed. The injected 503 and the local Vite favicon 404 were expected test-only results.

No new Issue candidate remained after checking existing Issues #47, #48, #54, #63 and #65. #47 was exercised only as synthetic Work data; its production-completion state was not verified by this pass. The browser used no production account, and ACL was inspected without saving changes.
