# DevAgent V1 maintainer

The maintainer runs on VM111, `192.168.0.111`, Ubuntu Server, 4 vCPU and 10 GB
RAM. It uses the independent `devagent` Unix user, with home `/var/lib/devagent`.
The existing `xeonolab@dev-agent` work/webhook API remains separate.

Runtime is the verified official OpenCode v1.18.34 Linux x64 baseline release,
SHA256 `24b0d458d21ef548b2752166303defcf7f4945b049fb4876ab78dfaf86d81b27`.
Default model is `opencode-go/deepseek-v4.1-flash`. Two actual independent
OpenCode Go sessions have passed repository/Issue reads and a document
edit/commit/push test. The latter commit is
`f8906edde38a2a7e61692bf676989b53c64f3ddd`, on `devagent/acceptance-v1`.

## Operator access

Use existing management SSH, then `sudo -iu devagent`. Run `devagent` for an
interactive TUI, or `devagent-msg "检查 PR #123"` for an independent manual
session. An actual SSH PTY opened the Maintainer / DeepSeek V4.1 Flash TUI and exited cleanly. No wrapper uses `--continue`. Automatic and manual work share a kernel
`flock` protecting the checkout. The runtime uses ephemeral CLI sessions; a
persistent public OpenCode server is unnecessary. At the user's request,
`devagent-web.service` serves the native Web UI without a password at
`http://192.168.0.111:4096`. It binds only the VM's LAN address; a separate atomic
nft filter accepts loopback/LAN `192.168.0.0/24` and rejects all other sources.
No Cloudflare/Caddy route is added. Its default working directory is the
independent `devagent/web-manual` worktree `/var/lib/devagent/web-checkout`;
the shared OpenCode session store lets the operator inspect existing CLI sessions.
The Web service does not hold the automatic checkout lock. Do not manually
switch a Web editing session to the automatic checkout while a cycle is active.

Before the first service-mode run, create a root-owned
`/etc/devagent/opencode/.gitignore` containing OpenCode startup exclusions
(`node_modules`, `package.json`, `package-lock.json`, `bun.lock`, `.gitignore`).
The configuration directory remains read-only to the model. Actual automatic
startup initially failed without this file, unlike manual mode.

Installed CLI commands are root-owned symlinks to
`/usr/local/libexec/devagent-controller`. Policies live in `/etc/devagent`.
The controller disables project configuration and external plugins, and supplies
the installed policy as inline configuration. Explicit manual tasks have 48 steps; automatic coordination tasks have at most
12 per event, disable the editing tool and development/Git write commands, and route code
fixes to other Agents through Issues. Automatic review, guarded merge and protected
deployment are enabled. Preflight submission and deployment completion use separate
events so the model does not spend steps polling. Routine coding is not delegated
back to OpenCode Go. Repository and Issue text remain untrusted data. Blocking questions and
evidence belong in the relevant Issue/PR, after inspecting existing tools.

## Event loop and budget

`devagent-watch.timer` is configured for approximately two-minute incremental
checks; `devagent-reconcile.timer` performs full reconciliation every thirty
minutes. Both units use 2 CPU cores and at most 6 GB RAM. SQLite fingerprints
track PR heads, reviews/comments, merge state and CI, plus Issues assigned to
the maintainer or labelled `devagent`. Own comments and the embedded repository
timestamp in Forgejo combined-status responses do not trigger a model call.
Ordinary unrelated branch pushes therefore do not wake every PR.
Closed PR fingerprints ignore moving historical branch references. New project
events supersede older pending/failed retries for the same resource; a completed
new head must not be followed by a retry of an obsolete event. A CLI exit
code of zero only completes a model task if its last step finished with `stop`;
permission-interrupted tool sessions remain failed. Pending CI is reported once and the event ends; repeated sleep/poll loops were
observed in acceptance and stopped. A new CI state triggers the next cycle.
The wrapper sets the actual
repository directory explicitly instead of inheriting a login shell's home.
Hitting the configured step limit is also failed/incomplete, even when OpenCode
returns a normal summary with `stop`. Investigation or a planned fix does not
prove the requested code change, verification or publication was delivered.

Baseline/repeated live checks have returned zero pending work without a model
call. Failed automatic tasks have a thirty-minute cooldown and three attempts;
kernel locks release on process exit. Manual failures are not automatically
replayed as project events. Session IDs, returned token counts and cost are
recorded. The initial monthly threshold is $30, half the documented $60 Go
DeepSeek V4.1 Flash allowance; account-wide consumption can reduce available
provider quota. A timer itself never causes an LLM request.

## Forgejo and privilege boundary

`devagent-maintainer` is restricted, has no site-admin role, and has write access
only to `xenon-admin/XeonoLab`. Its access token is repository scoped to
`write:repository` and `write:issue`. Git HTTPS uses an installed askpass helper,
private token storage and the LAN CA; credentials are absent from the remote URL.

`devctl` provides repository reads, comments, PR writes, reviews, guarded merge,
validation, deployment and typed infrastructure operations. Bodies arrive on
stdin. Merge requires an approval for the fresh head, successful current-head
CI and the protected validator status; Forgejo receives `head_commit_id` to
reject a concurrent push. Validation is published by the separate non-admin
`devagent-validator` identity, whose token is root-owned.

The model user has exactly one sudo entry point:
`/usr/local/libexec/devagent-privileged` with no arguments and a bounded JSON
stdin protocol. PVE and staging keys are stored under root-only
`/etc/devagent/secrets`. The PVE key uses a root-owned forced-command receiver,
limited to the XeonoLab VM inventory. Read status/config/pools, create rollback
snapshots, start VMs, adjust bounded resources and grow disks are typed commands.
Changes require a project reason; there is no arbitrary shell or VM deletion
command. PVE status 111 has passed; private-key readability and unrelated sudo
have been rejected as expected. Existing administrator keys remain available.
Each application target has a separate `devagent-stage` Unix identity with a
forced command and one fixed sudo receiver. Root SSH login remains disabled on
those targets. The dedicated private staging key stays root-only on VM111; its
public key is restricted to VM111's address. Management SSH is preserved.

## Exact-SHA deployment mechanics

The intended production entry point is
`devctl deploy xenon-admin/XeonoLab EXACT_40_HEX production`. The root-owned
validator fetches the explicit commit and runs locked dependencies, lint,
pytest, service smoke/failure-path checks, OpenAPI comparison, frontend unit and
real browser checks, and a frontend build as `devagent` in bounded transient
units. Project code never builds as root. The deployment runner preserves current
versions, checks PVE/target health, registers/approves an artifact digest, takes
the existing exclusive Deploy Gateway lease, stages fixed targets and invokes
the existing activation executor. Frontend archive timestamps/ownership are normalized before hashing, so a
repeat validation of identical compiled bytes does not change the immutable
release digest or collide with an already staged exact SHA.
After the executor confirms a terminal deployment, the fixed staging receiver
on VM106 restarts and checks Deploy Gateway while the same lease still excludes
other deployers. It requires the current staged SHA, a matching activation record,
a terminal database record and the original live holder/epoch. This avoids
interrupting the Gateway's own activation request and ensures the new Gateway
code is loaded before releasing the lease. A failed Gateway check also enters
the existing rollback path and reloads the prior Gateway before lease release.
Its readiness failure path restores the
previous immutable release. Protected audits record task, SHA, artifact, lease,
previous versions, result and verification.

`devctl validate EXACT_40_HEX` and deployment return a background operation ID.
Use `devctl operation ID` for state and `devctl operation-logs ID` for a bounded,
redacted validation-log excerpt. Submission is not completion. Operations are
serialized, run under a two-hour transient-unit limit, and retain root-owned
requests/results/audits. A crashed deployment is recorded as interrupted and
requires operator recovery rather than automatic replay. Before clearing that
flag, inspect target versions, the Gateway/executor lease and deployment audits.

Node v24.21.0 and Chrome 150.0.7871.186 are installed for verification. The
current frontend lockfile has a local dependency seed because npm egress remains
blocked (#87); changed lockfiles still require a reachable dependency source.

## Handoff status — 2026-10-01 UTC

The DevAgent V1 runtime is deployed on VM111. Production commit
`4372e9ea43e4150a858bb73c52635ea5bc55830b`, release
`04fbd36b-8a08-4c85-ab39-72bfe9d4b2c0`, and deployment
`fb33352a-c146-48dc-a630-4b5e8b0d19c4` completed through the Deploy Gateway;
the lease was released. All five application readiness endpoints returned 200,
all target VMs resolved to the deployed SHA, and public/LAN desktop assets
matched the release build. VM111 was rebooted after deployment; the DevAgent API,
OpenCode Web, and firewall returned active. OpenCode Web is intentionally
passwordless and bound to `192.168.0.111:4096`; nftables permits the port only
from `192.168.0.0/24`. The installed OpenCode version is 1.18.34.

The two systemd timers are enabled: incremental watch every two minutes and
full reconciliation every thirty minutes. Before enabling them, the current
Forgejo state was recorded with a model-free baseline. The baseline and first
timer run each reported zero changes; the automatic queue had no pending work
and no `model.started` audit event was recorded. The automatic prompt is
able to review and publish accepted PRs; it routes code fixes to other project
Agents and does not edit or test code itself. Nine historical failed manual tasks
remain excluded from automatic retries; they do not create timer work.

### Timer restart recovery finding — 2026-10-02

Production journal review found the watch timer had been stopped at 18:37:09
UTC and restarted at 18:37:36 after its `OnUnitInactiveSec` deadline had
passed. It then remained elapsed and did not trigger another watcher run.
`Persistent=true` does not recover missed monotonic timer events; it applies to
`OnCalendar` schedules. The source fix changes both recurring timers to explicit
UTC calendar schedules while retaining their boot checks. After installing the
updated units, verify each timer's effective calendar, persistence setting and
next deadline, then record two unattended timer/service activations per timer.
Test missed-run recovery separately after the boot check has passed: stop a
timer across a calendar deadline, restart it, and confirm one catch-up service
run and a future calendar deadline. Persistence schedules a catch-up run; it
does not replay every missed occurrence. Allow for `RandomizedDelaySec` and
systemd's timer accuracy window when checking timestamps.

The DevAgent identity successfully queried Forgejo Issue #77 and used the typed
PVE interface to read VM111 status (`running`). Its Git HTTPS push permission
passed a `--dry-run` probe; Forgejo confirmed the probe ref was not created.
The DevAgent checkout was clean and fast-forwarded to the deployed SHA. A
read-only production search for `积分` returned the existing XeLibrary record
with an explicit approximate-result marker; Issue #77 was then closed with the
deployment and verification evidence.

The root-owned PVE key remains mode 0600 and unreadable by the `devagent` Unix
user; an arbitrary `sudo id` attempt by that user was rejected. No exact
credential values were found in the scanned service journals or private logs,
and repository credential files are not tracked. A successful prior
`devagent-msg` session is present in the private audit; no new model request was
needed for that initial release acceptance. That production activation was executed by Codex through
the existing lease-protected gateway. We did not deliberately induce a
production deployment failure or exercise rollback by interrupting a live
release; the tested rollback path remains available in the fixed executor.

Keep the Goal active until the final operator handoff and any remaining
permission-boundary checks are recorded. For human takeover, stop both timers
and any current model unit, preserve root audits and use the existing
administrator SSH/Deploy Gateway access. Never force-clear a lease until the
executor has confirmed the previous deployment ended. Source updates to the
Agent itself do not silently replace its installed root-owned safety policy or
privileged helpers.

## Autonomous publication — 2026-10-02

OpenCode may now review, merge and deploy valid PRs; automatic code fixing remains
assigned to other Harnesses to conserve Go quota. Semantic review must inspect the
linked Issue, actual diff, related source and tests. Green tests alone do not prove
every business change correct. Rejected work returns to its author via PR review/Issues.

The merge target is fixed to `feature/platform-v1`. The helper refuses wrong targets,
drafts, missing required CI contexts, stale/dismissed approvals and unresolved change
requests. It fails closed if the branch is not protected. Operator setup:
`uv run python scripts/configure_devagent_branch_protection.py --apply`; omit `--apply`
to check the installed rule against `infra/devagent/branch-protection.json`.
The rule blocks direct pushes, requires backend/frontend PR CI plus protected preflight,
restricts approvals and merges, dismisses stale approvals and blocks outdated branches.
It applies to administrators too; emergency intervention requires an explicit operator
alteration of the rule, rather than force merging.

After a guarded merge, SQLite retains the publication intent and merge SHA. The watcher
recovers the merge-to-deploy gap even though closed historical PRs remain excluded.
A recorded queued/running deployment is polled deterministically without waking a model;
a terminal result wakes one reporting event. Duplicate deployment requests return the
recorded operation instead of creating another rollout. Failed/interrupted/rolled-back
operations require operator investigation and block the next automatic merge. A
superseded merge commit is not deployed. The root runner checks the freshly fetched
integration tip before taking a lease and again after staging, before activation.

A queued validation/deployment is submission, not completion. Automatic events finish
without model sleep/poll loops; CI and publication result changes trigger continuation.
Report success only for `result=completed` with every verification value `ok=true`.
The root broker continues to run deterministic tests/builds, own production credentials,
acquire the exclusive Deploy Gateway lease, stage fixed targets and verify/roll back.
Trusted controller, installed policy and root receiver updates still require reviewed
operator installation; application deployment cannot rewrite those privilege boundaries.
