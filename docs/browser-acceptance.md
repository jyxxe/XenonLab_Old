# Desktop browser acceptance

`scripts/browser_acceptance.mjs` runs the desktop UI in the installed Google Chrome through its
local DevTools protocol. It uses Node's built-in `WebSocket` and has no npm dependencies. It
captures login, authenticated desktop, LaunchPad, Knowledge, attachment, ACL settings, and identity
and permissions pages; reports console/page exceptions, HTTP errors, and horizontal overflow
measurements; and writes screenshots plus a JSON report to the requested output directory.

The test performs the normal OIDC login/session exchange and read-only UI actions (opening apps,
selecting the first visible table/row, and changing settings tabs). It does not create records,
edit ACLs, upload/bind attachments, save settings, or call mutation APIs for business resources.
Use only a temporary test human account with a seeded readable table/row. The ACL tab requires
`xelibrary:admin` to inspect editable controls; without it the UI should show the read-only gate.

## Browser and certificate preflight

- Google Chrome must be installed (`/usr/bin/google-chrome` by default).
- Node.js 20+ is required for the built-in WebSocket API (tested with Node 24).
- `certutil` from NSS tools must be available, or passed with `--certutil /path/to/certutil`.
- Before running, the coordinator imports the LAN root CA into the real current user's
  `~/.pki/nssdb` using a unique nickname and SSL CA trust. The script only reads this database to
  verify that nickname and trust entry exist; it never imports, deletes, or alters certificates.
  Chrome runs with a temporary browser profile but the current `HOME` unchanged, so Linux Chrome
  uses that user's NSS certificate database. The script has no `--ignore-certificate-errors`
  option or equivalent.

Example private credential file (create with mode `0600`; substitute temporary account data and
the exact CA nickname the coordinator imported):

```json
{
  "appUrl": "https://192.0.2.10:9443/",
  "username": "temporary-test-user",
  "password": "replace-with-temporary-password",
  "caNickname": "XeonoLab local acceptance CA"
}
```

Run after the temporary test account exists:

```sh
chmod 600 /path/to/private-browser-account.json
node scripts/browser_acceptance.mjs \
  --config /path/to/private-browser-account.json \
  --out /path/to/private-output \
  --chrome /usr/bin/google-chrome \
  --certutil /path/to/certutil \
  --ca-nickname 'XeonoLab local acceptance CA'
```

The script never prints the private config or credentials. It deletes its temporary Chrome
profile on exit. Screenshot files and `report.json` are written with mode `0600`; screenshots
may contain test-account subject names and test knowledge data, so keep the output directory
private and remove it after review. Errors such as a missing CA entry, Chrome TLS rejection,
missing readable data, or an OIDC selector change are reported without disabling certificate
validation.

The local checkout has Chrome and Node installed but no Playwright/Puppeteer/Selenium packages and
no `certutil` on PATH. A rootless extracted `certutil` is available at
`/home/jyx/projects/XeonoLab/runtime/browser-tools/extracted/usr/bin/certutil`; the script accepts
that path and leaves CA import/removal to the coordinator. Syntax is checked with `node --check`;
actual UI execution is pending the coordinator's trusted NSS entry and temporary human account.
