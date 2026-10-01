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
session. No wrapper uses `--continue`. Automatic and manual work share a kernel
`flock` protecting the checkout. The runtime uses ephemeral CLI sessions; a
persistent, publicly listening OpenCode server is unnecessary.

Installed CLI commands are root-owned symlinks to
`/usr/local/libexec/devagent-controller`. Policies live in `/etc/devagent`.
The controller disables project configuration and external plugins, and supplies
the installed policy as inline configuration. The maintainer has 32 steps per
task. Repository and Issue text remain untrusted data. Blocking questions and
evidence belong in the relevant Issue/PR, after inspecting existing tools.

## Event loop and budget

`devagent-watch.timer` is configured for approximately two-minute incremental
checks; `devagent-reconcile.timer` performs full reconciliation every thirty
minutes. Both units use 2 CPU cores and at most 6 GB RAM. SQLite fingerprints
track PR heads, reviews/comments, merge state and CI, plus Issues assigned to
the maintainer or labelled `devagent`. Own comments and the embedded repository
timestamp in Forgejo combined-status responses do not trigger a model call.
Ordinary unrelated branch pushes therefore do not wake every PR.

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

## Exact-SHA deployment mechanics

The intended production entry point is
`devctl deploy xenon-admin/XeonoLab EXACT_40_HEX production`. The root-owned
validator fetches the explicit commit and runs locked dependencies, lint,
pytest, service smoke/failure-path checks, OpenAPI comparison, frontend unit and
real browser checks, and a frontend build as `devagent` in bounded transient
units. Project code never builds as root. The deployment runner preserves current
versions, checks PVE/target health, registers/approves an artifact digest, takes
the existing exclusive Deploy Gateway lease, stages fixed targets and invokes
the existing activation executor. Its readiness failure path restores the
previous immutable release. Protected audits record task, SHA, artifact, lease,
previous versions, result and verification.

Node v24.21.0 and Chrome 150.0.7871.186 are installed for verification. The
current frontend lockfile has a local dependency seed because npm egress remains
blocked (#87); changed lockfiles still require a reachable dependency source.

## Handoff status

This runbook records bootstrap progress, **not completed production handoff**.
Automatic timers are deliberately not enabled until the deployment and merge
acceptance is complete. Outstanding acceptance includes full protected
preflight/PR review/merge, production staging/activation/rollback verification,
interactive TUI, actual restart recovery, final secret/log audit and updated
operator handover evidence. Keep the Goal active until those checks pass.

For human takeover, stop both timers and any current model unit, preserve root
audits and use the existing administrator SSH/Deploy Gateway access. Never
force-clear a lease until the executor has confirmed the previous deployment
ended. Source updates to the Agent itself do not silently replace its installed
root-owned safety policy or privileged helpers.
