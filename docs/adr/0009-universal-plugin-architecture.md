# ADR 0009 — Universal plugin architecture (Phase 0 contracts)

Status: accepted (Phase 0 delivered; Phase 1 registry MVP started). Supersedes nothing; extends ADR 0008
(plugin catalog) from desktop UI extensions to a platform-wide capability model.

## Context

The desktop already has a declarative plugin catalog, per-account installs, app
windows and widgets (ADR 0008, `docs/api/*`, `GET /api/v1/plugins`). Backend
modules (XeLibrary, Embedding, dev-agent work queue, Tool Gateway, LLM/embedding
providers) are still registered in code. Epic #305 wants a gradual microkernel
evolution: identity, authorization, audit, plugin management and capability
registration stay in the trusted core; business capabilities become plugins that
can be discovered, version-negotiated, enabled, disabled, upgraded and replaced.

This ADR fixes only **Phase 0 contracts**; no runtime behaviour changes yet.

## Decision

### 1. Microkernel boundary

The trusted core keeps: OIDC identity/session, account tree and scopes, audit,
secrets/CA handling, plugin and capability registry, deployment/approval gateway,
HTTP/gateway edge and the desktop shell. Everything else is a capability plugin.

A module may ship both core and plugin parts (e.g. XeLibrary storage is core
infrastructure while its query/theorem features are capabilities), but the split
is declared in the manifest, not implied by repository layout.

### 2. Unified capability model

Every capability is `namespace.capability` (lowercase, `[a-z0-9._-]`, namespace
owned by the manifest id). Initial kinds:

| kind | contract | current provider |
| --- | --- | --- |
| `app` | desktop window/app entry (`GET /api/v1/apps` semantics) | builtin + iframe plugins |
| `widget` | desktop widget id usable in preferences | builtin shell widgets |
| `tool` | Tool Gateway tool invocation | tool-gateway builtins |
| `agent` | task/agent endpoint (e.g. Library Agent) | none yet |
| `provider` | model/embedding provider behind a stable interface | D100M, Qwen worker |
| `worker` | background job processor | index/embedding/bulk workers |

A plugin declares one or more capabilities; each capability carries its own
version, permission scopes, dependency list and runtime tier. Capability ids are
stable API; breaking a capability requires a new major version and a new id.

### 3. Manifest schema and versions

Machine-readable contract: `infra/plugins/capability-manifest.schema.json`
(JSON Schema 2020-12). Manifest fields: `id` (namespace), `version` (semver),
`api_version` (registry contract, currently `1`),
`capabilities[{id, kind, version, permissions, dependencies, runtime}]`,
`display`, `source` (builtin/declared), and `config_schema` hash when applicable.
The registry rejects unknown `api_version`, duplicate capability ids, invalid
semver, capability ids outside the plugin namespace, and dependency cycles.

### 4. Registry and invocation protocol

`CapabilityRegistry` (Phase 1) is a read-only aggregation over the builtin
registry and installed plugin records: `capabilities()`, `lookup(id, version
constraint)`, `enabled(account)`, `compatible(requested)`. Callers never invoke
plugins directly from business code: they resolve a capability contract through
the registry, which returns a typed adapter plus the effective permission set.
Invocation failures are isolated per capability kind (UI renders an error state,
backend calls return the existing typed `APIError` codes) and never crash the
kernel.

### 5. Lifecycle states

`published` (manifest present) → `installed` (per-account record) → `enabled`
(account and platform gates pass) → `authorized` (scopes granted) → `running`.
Disable, upgrade, rollback and uninstall are explicit transitions with audit
events; disabling a plugin hides its capabilities from discovery and rejects new
invocations while in-flight work is bounded by per-capability timeouts. Rollback
restores the previous manifest/version; capability data migration is the
plugin's responsibility and must be forward/backward compatible or refused.

### 6. Runtime tiers

- `ui` — declarative app/widget (desktop window or sandboxed iframe; no platform
  process access). Existing iframe sandbox/permissions stay authoritative.
- `api` — in-process Python adapter registered through the registry interface;
  trusted code, permission-checked, bounded by the shared FastAPI process.
- `service` — independent deployed service (Phase 3) reachable only through the
  edge/gateway with service identity, quotas, health checks and **no** automatic
  code execution rights.

Runtime tier is declared per capability; the registry never upgrades an `ui`
capability into `api`/`service` privileges.

### 7. Threat model (Phase 0 minimum)

Assets: credentials (OIDC clients, Forgejo tokens, provider keys), user data
(XeLibrary tables/attachments), audit trail, deployment authority.

Threats and required controls:

| threat | control |
| --- | --- |
| malicious manifest/namespace collision | schema validation, namespace ownership, duplicate rejection |
| privilege escalation via capability permissions | capability permissions ⊆ plugin's registered scopes; account scopes re-checked per call |
| dependency confusion / downgrade | exact version constraints, registry allows only known manifests, no fetching at runtime |
| disabled plugin still invocable | registry resolution + per-call enablement check, tests for denial |
| plugin crash/latency | per-capability timeouts, error isolation, no shared mutable state in adapters |
| supply-chain tampering of service plugins | Phase 3: signed packages, digest pins, approval-gated deployment |
| secret leakage in manifests | manifests carry no secret values; secrets referenced by name only |

Security tests required before Phase 2: denial after disable, permission
escalation attempt, unknown api_version, duplicate capability, dependency cycle,
timeout isolation.

### 8. Compatibility and migration

`GET /api/v1/plugins` and `GET /api/v1/apps` keep their current response shapes.
Builtin apps/widgets are described in a builtin manifest set emitted by the
registry, so Dock/LaunchPad/window code moves from hardcoded lists to the
registry without changing URLs or permissions. Existing plugin records remain
valid; the registry maps them to `app` capabilities. Phase 2 introduces adapter
interfaces beside current call sites behind feature flags; no breaking change is
allowed without an ADR update and a deprecation window.

### 9. Phase 0 deliverables

- this ADR;
- `infra/plugins/capability-manifest.schema.json` plus a validated example;
- contract tests (`tests/test_plugin_manifest.py`) covering schema validation,
  namespace/version/duplicate/dependency rules;
- follow-up Phase 1 issue with registry endpoint and builtin migration example.

Phase 1 progress: the registry aggregates builtins and account-scoped enabled
plugin UI capabilities (#315) and negotiates versions (#316). Desktop discovery
now gates native adapters, configured iframe apps and widgets through that
projection (`docs/issue305-desktop-discovery.md`); legacy descriptor APIs retain
URLs and sandbox contracts. Administrator-managed apps use source `registered`
and UI capabilities carry a `desktop_id` descriptor join. PR #318 separately
aligns detail resolution with account enablement/permissions. The declarative
builtin migration example is tracked by PR #317. Phase 2 adapter contracts and
Phase 3 service-runtime security design remain separate milestones.

## Consequences

Phase 1 can implement the registry without touching runtime behaviour. Adapter
interfaces for Tool Gateway/providers must be designed per contract before any
business module moves, and service plugins remain blocked on the Phase 3
security design and Deploy Gateway integration.

### Phase 2: first trusted LLM Provider migration (#322)

`ChatProvider` / `PreparedChat` separate static provider configuration and canonical
HTTP transport from the gateway's identity, input/SSE validation, deadlines and
resource ownership. A trusted `ChatProviderRegistry` checks provider/api capability,
llm:use, version and process-local enablement; it does not execute manifest code.
`LLM_CAPABILITY_ADAPTERS` enables resolution (default 1) or selects the original
HTTP binding (0). JSON now shares the gateway's absolute deadline/error boundary.
Details, process-local lifecycle limits, rollback and validation are recorded in
`docs/issue322-chat-provider.md`. Persistent cross-service lifecycle coordination,
Embedding adaptation and service runtime security are still separate work.

## Phase 3 design proposal

[ADR 0010](0010-service-plugin-runtime-security.md) proposes the independent
service admission, identity, execution-policy and fenced lifecycle boundaries.
Its offline declaration contract is not a runtime registry, deployment grant or
security acceptance. Existing `runtime=service` metadata and Phase 2 trusted
adapters must not enable third-party server code execution implicitly.

### Phase 2: first trusted tool adapter migration (#320)

`xeonolab/tool_adapters.py` introduces a static async tool binding and resolution
contract. Tool Gateway search/query/get resolve declared tool/api capabilities,
scopes, version requirements, live process-local enablement and dependencies on
each invocation. This does not load executable code from plugin manifests or
expose remote registration/lifecycle APIs. `TOOL_CAPABILITY_ADAPTERS` selects the
registry path (default 1) or legacy rollback path (0). Async budgets bound trusted
cooperative I/O only; service/CPU isolation still belongs to Phase 3. Detailed
permissions, error semantics, lifecycle limits and rollback are recorded in
`docs/issue320-trusted-tool-adapters.md`. Embedding/LLM Provider adaptation and
XeLibrary parsing/index extensions remain subsequent Phase 2 work.

### Phase 2: first XeLibrary indexing extension (#326)

`ChunkerBinding` / `DocumentChunkers` wrap the existing tokenizer-aware text
processor with a static worker/api capability, admin gate, derivative identity
and cooperative output/time budgets. Default `chunker-v2` remains compatible;
replacement identities propagate through point ids, local/remote metadata and
all read revalidation gates. The kernel keeps ACL/source/transaction authority;
processor output is not a public fact snippet. `XELIBRARY_CHUNKER_CAPABILITIES`
controls gates (default 1); hard CPU/service isolation and persistent lifecycle
coordination remain separate work. Contract, changed bounds, rollback and
validation: `docs/issue326-document-chunker.md`.

### Phase 2: trusted Embedding Provider migration (#324)

`EmbeddingProvider` / `PreparedEmbedding` separate default D100M/Qwen workers from
HTTP callers, queue/metrics and result serialization. Static bindings use separate
describe/read and encode/write capabilities to preserve existing write-only
clients. Startup-only replacement and process-local enablement do not load code
from UI manifests or create a persistent cross-service lifecycle system.
`EMBEDDING_CAPABILITY_ADAPTERS` selects manifest gates (default 1) or bypasses them
(0); endpoint scopes remain. Qwen keeps process-backed deadlines; no hard D100M
CPU deadline is claimed. Ownership, compatibility, fault handling, rollback and
validation are described in `docs/issue324-embedding-providers.md`. #312's actual
model replacement, lifecycle coordination and Phase 3 isolation remain separate.



## Persistent lifecycle increment (#331)

The [query/LLM/embedding/index lifecycle increment](../issue331-capability-lifecycle.md) adds
transactional controls to the account authority and passes a versioned snapshot
through its existing live authorization channel. Query, LLM, embedding and index admission honor it; index jobs additionally
reauthorize at bounded cooperative checkpoints; mutation is an explicit, default-off rollout gated by a dedicated human
grant. Other Provider/Worker controls and Phase 3 service runtime remain unintegrated.
This does not turn process-local adapter hooks into a global controller or imply
that old consumer binaries can safely ignore a disable. See the rollout and
rollback prerequisites before enabling controls.
