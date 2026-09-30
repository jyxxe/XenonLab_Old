# ADR 0003 — Account tree authority

Keycloak remains the password and signed identity issuer. The desktop runs a
persistent account authority for managed account trees, effective permissions,
revocation and opaque API keys. Its dedicated service client may manage realm
users; its secret is installed only on VM 101, never supplied to Agents. This
is a privileged server dependency, not a master administrator credential.

Only a verified human desktop administrator can bootstrap their own root.
Managed children are created by their ancestors with account-management rights.
No reparent operation is exposed; trees are bounded at 128 levels. Partial
inheritance intersects explicit grants with every ancestor. Full inheritance
tracks the parent's entire current set. A child cannot edit its own inherited
policy or a sibling/ancestor. Root accounts retain desktop administration.
Existing operator-issued OIDC service identities remain separate and preserve
signed agent_kind; registering users does not let OpenCode become Codex.

All API services check the fixed TLS account authority on each request, without
caching effective permissions. JWT signature/issuer/audience checks still occur
locally before authority lookup. Authority unavailability fails closed. API
keys are random opaque values, stored as hashes and shown once; issued scopes
are capped by both issuer and owner's effective permissions, and shrink with
the account tree. API keys cannot reset passwords or alter account policies.

Password reset fences old JWTs, revokes the user's keys and deletes browser
sessions before calling Keycloak. Provider timeout returns a pending/error
rather than restoring old credentials. Keycloak reset also logs out SSO
sessions. Retrying may require fresh login or an ancestor. Password changes via
an external Keycloak admin do not update this local fence; platform password
reset should use the platform API. Full identity reconciliation remains work.

Deploy Gateway retains its independent Codex-kind, approval and lease checks;
a human or OpenCode with delegated scopes cannot execute a production release.
The authority is activated first in new release inventories. Deploy Gateway's
long-running process is restarted only after its in-flight activation finishes.
