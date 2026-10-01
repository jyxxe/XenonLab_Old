# Public browser acceptance — 2026-10-01

A browser-role Luna used real headless Google Chrome 150.0.7871.186 against
`https://xenon.jyxxe.com`, with 1440×900 and 390×844 viewports. No login form or
business change was submitted; the role had no production credentials. The
coordinator independently inspected mobile login and keyboard screenshots.

Public Forgejo landing, Explore, repository, Issues, PR and login pages rendered
with CSS; Forgejo reported 16.0.5 and the source page showed `c6691eacbe`. All
inspected pages had document width equal to viewport width. Login labels and
keyboard focus were visible. There were 93 HTTP 200 and 10 HTTP 204 responses,
with no failed requests, HTTP errors, console errors or page exceptions. The
204 responses were automatic Cloudflare RUM beacons.

The desktop login button reached the real Keycloak login page. Desktop and
login rendered without horizontal overflow at both widths; browser Back
returned to the desktop, Forward restored the login page, and username,
password, show-password and sign-in keyboard focus remained visible. This
second probe recorded 38 HTTP 200, six RUM HTTP 204, and three expected
unauthenticated `/api/v1/auth/session/token` HTTP 401 probes, with corresponding
browser resource messages. No application exception or visible error appeared.

No new defect was substantiated. Existing #85 concerns the authenticated task
WebUI filter bar, not Forgejo's mobile Issues page. These guest checks do not
prove authenticated workflows, long-lived sessions or all-browser support.

Private reports and screenshots:
`runtime/qa-cycle-browser-private/public-forgejo-2026-10-01/` and
`runtime/qa-cycle-browser-private/public-desktop-2026-10-01/`.
