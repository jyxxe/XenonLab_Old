# Forgejo CI runner

Issue #82 implements a runtime regression gate; #87 tracks independent production
download/egress. Do not mark CI accepted until the exact workflow executes on
Forgejo and works without an operator workstation or temporary SSH session.

## Current infrastructure (2026-10-01)

VM 111 has a dedicated forgejo-runner unix user, rootless Podman socket and linger.
Forgejo Runner 13.2.0 was verified against release signing key
EB114F5E6C0DC2BCDD183550A4B61A2DC5923710 and SHA-256
fadaec897f5e6641c363f87ecaf75f866f99c81b7097ec6b433e4c48123fcad1 before transfer.
The operator-only infra/forgejo/install-runner.sh pins that digest, checks transfer
integrity, installs packages/binary and enables only Podman. Invoke as root with
the prepared binary/checksum in /tmp. It does not start the CI daemon.

Repository-scoped registration ID 1, xeonolab-dev-agent-ci-111, exists. Its token
is kept in operator-protected files and never supplied to Harnesses. Config,
token, environment and trusted CA bundle under /etc/forgejo-runner are managed
by root:forgejo-runner; directory 0750 and files 0640. Install the service from
infra/forgejo/forgejo-runner.service in /etc/systemd/system as root:root 0644;
daemon-reload and validate on VM 111. Starting/enabling is a separate operator
step after prerequisites are ready; the daemon is currently stopped.

Config uses server.connections with token_url pointing to the protected file,
exact LAN HTTPS and trusted CA bundle; insecure=false. Capacity is 1 and job
timeout 30 minutes. Containers are unprivileged, without arbitrary host-volume
or runtime-socket mounts, limited to 3 GiB/2 CPU/512 PIDs. The daemon service
limits are separately 512 MiB/1 CPU. Cached Node 24 Bookworm image is pinned to
docker.io/library/node@sha256:64af3819f9275802414d7cdc38c27e9d82bd564dec4d4da87d008255d36c63b4.

## Download prerequisite

Direct registry requests timed out or reset. System DNS differed from HTTPS DNS;
even direct requests to an HTTPS-resolved address reset, so changing hosts alone
is insufficient. A temporary loopback SSH reverse proxy reached registry/GHCR
authentication challenges. Podman initially received EOF; with
GODEBUG=tlsmlkem=0, image download completed and OCI digests were recorded.
TLS verification stayed enabled. This is path-specific evidence, not a proven
general network cause. See the [Go compatibility reference](https://go.dev/doc/godebug)
and [Forgejo runner guide](https://forgejo.org/docs/v16.0/admin/actions/installation/binary/).

The temporary proxy must be removed and must not become the production CI
dependency. #87 requires independent NetworkServer egress, constrained access,
TLS/DNS handling, startup/health evidence and actual CI verification after the
workstation tunnel stops. A cached image alone does not satisfy action/npm/uv/
Chromium download requirements. #82 remains open until actual execution.

## Operational acceptance

Verify runner scope/labels/status through the repository Actions API. Run actual
relevant PR/push workflows; inspect unit, browser smoke/regression and build
steps separately. Prove failure status and downloaded synthetic failure
artifacts. Missing Chromium must fail. Set branch check protection only after
real check names/results are verified. Record runner restart and dependency
download without temporary sessions.

To retire this runner, stop/disable forgejo-runner on VM 111 and remove only
repository registration ID 1 after verifying ownership. Do not stop DevAgent or
other application services.
