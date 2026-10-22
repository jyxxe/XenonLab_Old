# ADR 0008 — Declarative plugin catalog and per-account installs

- Status: Accepted for review (Issue #272)
- Date: 2026-10-20
- Scope: `desktop-web` service and desktop shell

## Context

Issue #272 asks for plugin support: desktop widgets, additional applications, per-user
installation, and the possibility of storing plugins in the cloud. The platform already has
an administrator-managed registry of external iframe applications (§18.1), a sandboxed
cross-origin window host, per-account desktop preferences, and OIDC scopes. It has no
mechanism for users to add capabilities to their own desktop.

"Plugin" can mean several very different things: server-side Python packages, browser
extensions, JavaScript bundles executed inside the desktop origin, or independently hosted
web applications embedded in a window. The last option is the only one that fits the existing
API boundary without granting a plugin access to the platform's process, filesystem, database,
or bearer tokens.

## Decision

1. **A plugin is a declarative manifest, not server code.** The service never imports,
   evaluates, downloads, or executes plugin code. A manifest declares at most one `app`
   (iframe window) and at most one `widget` (dashboard iframe), each with an absolute HTTPS
   origin and a relative entry path. Origins are validated with the same rules as the
   administrator app registry: no credentials/query/fragment, no desktop origin, HTTPS in
   production, and a bounded default size.
2. **The cloud catalog stores metadata only.** `desktop.sqlite` holds the catalog and
   per-account installations:
   - `plugins` — current manifest, version, owning app id, publisher, revision;
   - `plugin_installs` — `(owner, plugin_id)` with enabled state, the accepted manifest
     snapshot, catalogue revision and installed version;
   - `plugin_audit` — publish/update/delete/install/enable events.
   Catalogue edits do not move installed apps until the account explicitly reinstalls, so
   a new manifest cannot silently change an origin or add a permission.
   Publishing, updating, and deleting require `desktop:admin`; every account with
   `desktop:read` can install or uninstall a catalog entry for itself. Installations are
   idempotent and updates are explicit so a new manifest cannot silently add permissions.
3. **Requested scopes are checked at install and on every projection.** A manifest's
   `permissions` must be a subset of the installing account's current scopes. If scopes are
   later revoked, the plugin app/widget stops being projected. This is an authorization and
   visibility gate; it is **not** a token-brokering mechanism. No access token is passed to
   the iframe, so a plugin that needs platform data must perform its own OIDC login (its
   origin keeps its own cookies) or use a future, separately designed token exchange.
4. **Installed apps are read-only projections of the catalog.** `GET /api/v1/apps` appends
   enabled plugin apps for the calling account only. They are never inserted into the
   administrator `apps` table, so the app registry UI/API cannot edit or delete them.
   Plugin app ids are reserved against built-in apps and unique across the global registry
   and the catalog. `include_unavailable=true` intentionally excludes plugin apps.
5. **Widgets are opt-in per account.** `GET /api/v1/users/me/plugin-widgets` returns enabled
   widgets whose scopes still match. The user selects them through the backward-compatible
   `plugin_widgets` preference (maximum 8 ids); uninstalling removes the id. The shell
   renders each as a cross-origin sandboxed iframe with no token and no privileged
   `postMessage` bridge.
6. **Package hosting and code signing are deferred.** A catalog entry points at an
   independently hosted origin; the platform does not mirror the plugin's HTML/JS/assets.
   A future object-storage package store would need integrity hashes, signing keys, cache
   and revocation design, and a browser-execution threat model. None of that is implemented
   here, and the manifest's `homepage`/metadata must not be treated as verified.

## Consequences

- A compromised plugin origin can only attack its own sandboxed frame and the user's
  separate session on that origin. It cannot run in the desktop origin, read `localStorage`,
  call `parent` APIs, or obtain the platform bearer token through this feature.
- An administrator-curated catalog is a trust boundary: publishing a malicious origin still
  lets it phish inside the frame. Operators must review entries like external apps.
- There is no marketplace search, ratings, package archive, automatic updates, signature
  verification, or per-plugin runtime capability enforcement. `permissions` only gates
  install/visibility.
- Version updates are visible (`update_available`) and require an explicit reinstall.
  Deleting a catalog entry cascades installation rows; stale layout/dock references are
  filtered by the existing unknown-app handling, and stale widget prefs are ignored.
- The schema is additive: old databases get empty `plugins`/`plugin_installs`/`plugin_audit`
  tables and `plugin_widgets` defaults to `[]`.

## Alternatives considered

- **Server-side Python/JS plugin execution** — rejected: arbitrary code in the API process,
  no safe sandbox, violates the "agents behind API boundaries" rule.
- **Bundling plugin JavaScript into the desktop build** — rejected: supply-chain and origin
  isolation risk, requires rebuilds and defeats per-user installation.
- **Reusing the global `apps` table for installs** — rejected: loses per-account state and
  lets the admin registry mutate plugin entries.
- **Token exchange into iframes now** — deferred: would require an explicit capability
  grant, audience-restricted tokens, and a threat model. The current feature deliberately
  passes no token.
