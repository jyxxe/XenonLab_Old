# #305 Phase 2 / #320: trusted Tool Gateway adapters

## Contract and boundaries

`ToolAdapter` binds a tool name and capability id to a statically supplied async
handler, Pydantic input model, minimum permission set and required version.
`ToolAdapters` resolves the current validated builtin registry on every call.
Manifest strings never identify Python modules, imports or executable paths.
Installing a UI plugin does not create an adapter or grant backend tool access.

Resolution checks tool/api kind and tier, the adapter's minimum scopes, all
manifest scopes, adapter version compatibility and transitive dependencies.
Dependencies must be present and compatible in the single loaded manifest;
unresolved external-manifest dependencies fail validation until a trusted
multi-manifest resolver is implemented. Execution dependencies additionally need
registered, enabled, authorized static bindings. A manifest cannot weaken the
minimum permissions established by the trusted handler registration. Duplicate
names or capability bindings and synchronous handlers are rejected.

The current tools are:

| Tool | Capability | Existing required scope | Deadline |
| --- | --- | --- | --- |
| xelibrary.search | builtin.platform.tool.xelibrary-search | xelibrary:query | 30s |
| xelibrary.query | builtin.platform.tool.xelibrary-query | xelibrary:query | 30s |
| xelibrary.get | builtin.platform.tool.xelibrary-get | xelibrary:read | 30s |

The existing API routes, input models, function descriptions, upstream paths,
accepted bearer forwarding and UUID-normalized request id forwarding remain.
`get` still rejects non-UUID identifiers before upstream access and filters the
requested row fields after successful reads. Upstream HTTP status and JSON
payloads are preserved. The tools list keeps its existing function-schema shape;
when adapters are enabled it omits tools the caller cannot invoke by scope or
enablement, so query-only users no longer discover an unusable `get` tool.

## Lifecycle, timeouts and errors

Trusted kernel code may use `app.state.tool_adapters.set_enabled(name, bool)`.
Disable hides a tool from discovery and rejects subsequent invocations with
404 `CAPABILITY_NOT_FOUND`. There is deliberately no new remote lifecycle or
adapter-registration API. This initial lifecycle hook is process-local, resets
on restart, and is not a persistent backend plugin install system. Existing UI
plugin installation and permissions remain separate.

`asyncio.timeout` cancels cooperative async work at the validated per-capability
budget (1..300 seconds; the existing tools use 30). It is not process isolation
or a CPU preemption boundary: these handlers must remain trusted async I/O code.
Already-running calls may finish within their budget after a disable. Third-party
code, CPU workers and independent services still require Phase 3 security design.

| Failure | Behavior |
| --- | --- |
| unknown/disabled/unregistered tool | 404 CAPABILITY_NOT_FOUND |
| missing caller scope | existing 403 FORBIDDEN |
| adapter version requirement not met | 409 CAPABILITY_INCOMPATIBLE |
| model mismatch on internal invoke | 422 INVALID_TOOL_INPUT |
| kind/runtime/minimum permissions mismatch | 503 CAPABILITY_INVALID |
| manifest dependency lacks trusted binding | 503 CAPABILITY_DEPENDENCY_UNAVAILABLE |
| cooperative deadline or HTTP transport timeout | 503 CAPABILITY_TIMEOUT |
| unexpected handler or invalid upstream JSON | 503 CAPABILITY_FAILED |

Existing `APIError` codes, including UUID rejection, pass through unchanged.
Unexpected exception details, upstream bodies and request headers are not
included in new error messages. A failed adapter does not mutate the registry or
prevent another healthy tool from being invoked. Dependency resolution memoizes
validated bindings within a call to avoid repeated traversal of shared DAG nodes.

## Rollout and rollback

`TOOL_CAPABILITY_ADAPTERS=1` (default) selects registry invocation and discovery.
`0` selects the existing static legacy handlers and original list behavior;
invalid values fail startup. The flag is read from trusted process configuration,
not request data. It permits rollback of this migration while retaining the
original endpoint scope and UUID checks; the legacy path does not implement the
new capability lifecycle hook, deadlines or safe adapter error semantics.
A code revert is also possible. No database migration or secret is added.
The Docker recipe now copies the builtin capability manifest alongside the source
checkout: registry invocation requires that public resource at runtime. A staged filesystem built from the Docker recipe's actual COPY directives
successfully loads all three tool capabilities in an isolated Python process.
This checks resource availability only; `docker --version` fails with exit 127
(`docker: command not found`), so an image build and production validation have
not been performed.

The manifest validator now rejects malformed, duplicate, missing or incompatible
dependencies, non-object capability records and invalid timeout budgets. Stable
requirements cannot accidentally accept prerelease versions; exact version
requirements compare the complete version string. Other version range semantics
remain the existing registry contract (caret is same major, tilde same minor).

## Evidence limits

Tests use synthetic handlers, FastAPI TestClient and httpx MockTransport. They
exercise real service routing, authorization, forwarding, discovery, adapter
replacement without route changes, disable/re-enable, dependency gates, timeout
cancellation, safe errors and the legacy flag. They do not call production
XeLibrary, install an external backend plugin, or validate a real provider or
independent service runtime. No frontend or visual changes are made.


## Local validation

Python 3.12.14, repository dependency versions, no lockfile changes:

- `uv run --no-sync pytest tests/test_capability_execution_contract.py tests/test_tool_adapters.py tests/test_capabilities.py tests/test_plugin_manifest.py tests/test_services.py`: 45 passed.
- `mkdir -p runtime` followed by `uv run --no-sync pytest --basetemp runtime/review-test-tmp`:
  751 passed, 13 failed, 272.32 seconds. The exact failure-name set matches the
  unchanged baseline's 13 Lean verifier failures. This desktop account receives
  PermissionError 13 statting `/proc/1/ns/net` and `/proc/1/ns/mnt`; the baseline
  and this branch do not differ in Lean code or tests. No sandbox checks were
  bypassed. An initial full attempt lacked the runtime parent directory and
  produced fixture setup errors; that invalid run is not counted as acceptance.
- `uv run --no-sync ruff check xeonolab tests scripts`: passed (CI scope).
- `uv run --no-sync python scripts/export_openapi.py`: passed, no OpenAPI diff.
- `uv run --no-sync python scripts/smoke_services.py`: all 11 services passed.
- `uv run --no-sync python scripts/verify_failure_injection.py`: all passed.
- Staging the Docker COPY layout and importing registry in an isolated Python
  process: all three tool capabilities resolved. Docker CLI is unavailable
  (exit 127); no image build, container execution or production test performed.
- `git diff --check`: passed. Frontend and visual verification are not applicable
  to this backend-only change and were not rerun.
