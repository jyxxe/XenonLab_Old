#!/usr/bin/python3
"""Isolated PVE VM-archive restore drill for XeonoLab (issue #113).

Restores a `vzdump` archive into a scratch VMID, boots it with the NIC link
down (so a clone carrying the production hostname/static IP cannot reach the
LAN), verifies it through the QEMU guest agent, then destroys the scratch VM
and its thin volumes. The drill only ever reads production state; the only
mutations are on the scratch VMID supplied on the command line.

Safety model:

* dry run by default: preflight checks run read-only and the planned steps are
  printed; nothing is created until ``--execute`` is passed;
* the scratch VMID must be in 112..989, outside the production/reserved ranges,
  and not in use;
* the archive must be a real volume on an active backup-capable storage;
* the clone is always started with ``link_down=1`` and ``onboot=0``;
* on any failure after the restore the VM is left in place for inspection
  (printed instructions) instead of being destroyed; successful drills destroy
  the VM and verify nothing is left behind.

Usage (from the DevAgent host, token file mode 0600):

    uv run python scripts/pve_restore_drill.py \\
      --archive 'xeonolab-backup:backup/vzdump-qemu-106-2026_11_04-08_17_19.vma.zst' \\
      --source-vmid 106 --vmid 190 --storage local-lvm \\
      --token-file ~/.config/devagent/pve-api-token --insecure --execute

Exit code 0 = drill completed and cleaned up, 2 = safety/usage refusal,
1 = drill failure (scratch VM may need manual cleanup).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PRODUCTION_VMIDS = frozenset(range(100, 112)) | frozenset(range(990, 1000))
SCRATCH_VMID_MIN = 112
SCRATCH_VMID_MAX = 989
ARCHIVE_VOLID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*:[a-z]+/[^/]+\.(?:vma(?:\.[a-z0-9]+)?|tar(?:\.[a-z0-9]+)?)$"
)
VMA_ARCHIVE_SUFFIXES = (".vma", ".vma.zst", ".vma.gz", ".vma.lzo")


class DrillError(RuntimeError):
    """A drill precondition or PVE operation failed."""


class SafetyRefused(DrillError):
    """The requested scratch target is not allowed."""


class PveClient:
    """Minimal PVE API client; the token never leaves the Authorization header."""

    def __init__(self, endpoint: str, token: str, *, ca_file: str = "", insecure: bool = False,
                 timeout: float = 120.0):
        self.endpoint = endpoint.rstrip("/")
        self._token = token
        self.timeout = timeout
        if insecure:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context = ssl.create_default_context(cafile=ca_file or None)
        self._context = context

    def request(self, method: str, path: str, data: dict | None = None):
        url = self.endpoint + "/api2/json" + path
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        if method == "POST" and body is None:
            body = b""
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Authorization": "PVEAPIToken=" + self._token},
        )
        try:
            with urllib.request.urlopen(request, context=self._context, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", "replace")
            raise DrillError(f"{method} {path}: HTTP {error.code} {detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise DrillError(f"{method} {path}: {error}") from error
        return payload.get("data")

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, data: dict | None = None):
        return self.request("POST", path, {} if data is None else data)

    def put(self, path: str, data: dict):
        return self.request("PUT", path, data)

    def delete(self, path: str):
        return self.request("DELETE", path)


def read_token(path: str) -> str:
    """Read a one-line ``tokenid=secret`` PVE API token and reject insecure files."""
    token_path = pathlib.Path(path).expanduser()
    try:
        info = token_path.stat()
    except OSError as error:
        raise SafetyRefused(f"cannot read token file {token_path}: {error}") from error
    if info.st_mode & 0o077:
        raise SafetyRefused(f"token file {token_path} must not be group/world readable (chmod 600)")
    token = token_path.read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9@._!-]+=[A-Za-z0-9-]+", token):
        raise SafetyRefused(f"token file {token_path} must contain exactly one 'tokenid=secret' line")
    return token


def check_target(vmid: int, used_vmids: set[int]) -> None:
    """Reject scratch VMIDs that could collide with production or in-use guests."""
    if not SCRATCH_VMID_MIN <= vmid <= SCRATCH_VMID_MAX:
        raise SafetyRefused(
            f"scratch vmid {vmid} is outside the safe range {SCRATCH_VMID_MIN}..{SCRATCH_VMID_MAX}"
        )
    if vmid in PRODUCTION_VMIDS:
        raise SafetyRefused(f"scratch vmid {vmid} is a production/reserved VMID")
    if vmid in used_vmids:
        raise SafetyRefused(f"scratch vmid {vmid} is already in use")


def validate_archive(volid: str) -> str:
    """Validate a backup volume identifier (storage:backup/file.vma.zst)."""
    if not isinstance(volid, str) or not ARCHIVE_VOLID.fullmatch(volid) or ".." in volid:
        raise SafetyRefused(f"not a backup volume identifier: {volid!r}")
    if not volid.endswith(VMA_ARCHIVE_SUFFIXES):
        raise SafetyRefused(f"not a VMA archive volume: {volid!r}")
    return volid

VZDUMP_NAME = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*):[a-z]+/"
    r"vzdump-qemu-(?P<vmid>\d+)-(?P<stamp>\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})"
    r"\.vma(?:\.(?:zst|gz|lzo))?$"
)


def select_latest_archive(volids: list[str], vmid: int) -> str:
    """Pick the newest ``vzdump-qemu-<vmid>`` VMA archive from a content listing."""
    matches = []
    for volid in volids:
        if not isinstance(volid, str):
            continue
        match = VZDUMP_NAME.fullmatch(volid)
        if match and int(match.group("vmid")) == vmid:
            matches.append((match.group("stamp"), volid))
    if not matches:
        raise SafetyRefused(f"no VMA archive found for vmid {vmid} on the archive storage")
    matches.sort()
    return validate_archive(matches[-1][1])


def resolve_archive(client: PveClient, node: str, archive_storage: str, vmid: int) -> str:
    """Resolve ``--latest-archive-for`` by listing the archive storage (read-only)."""
    backups = client.get(f"/nodes/{node}/storage/{archive_storage}/content?content=backup") or []
    volids = [entry.get("volid") for entry in backups if isinstance(entry, dict)]
    return select_latest_archive(volids, vmid)


def force_link_down(net0: str) -> str:
    """Return a net0 definition with ``link_down=1`` (replacing any existing value)."""
    if not isinstance(net0, str) or not net0.strip():
        raise SafetyRefused("VM has no isolated-able net0 device")
    options = [option.strip() for option in net0.split(",") if option.strip()]
    kept = [option for option in options if not option.startswith("link_down")]
    kept.append("link_down=1")
    return ",".join(kept)


def disk_sizes(config: dict) -> dict[str, str]:
    """Extract ``size=`` from every disk/first boot device in a VM config."""
    sizes: dict[str, str] = {}
    for key, value in config.items():
        if not re.fullmatch(r"(?:scsi|virtio|sata|ide)\d+", key) or not isinstance(value, str):
            continue
        match = re.search(r"(?:^|,)\s*size=([^,]+)", value)
        if match:
            sizes[key] = match.group(1)
    return sizes


def wait_task(client: PveClient, node: str, upid: str, *, timeout: float, poll: float = 2.0,
              sleep=time.sleep) -> dict:
    """Poll a PVE task until it leaves the running state; return the final status."""
    if not isinstance(upid, str) or not upid.startswith("UPID:"):
        raise DrillError(f"invalid task identifier: {upid!r}")
    quoted = urllib.parse.quote(upid, safe="")
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"/nodes/{node}/tasks/{quoted}/status")
        if not isinstance(status, dict) or "status" not in status:
            raise DrillError("unexpected task status payload")
        if status["status"] != "running":
            if status.get("exitstatus") != "OK":
                raise DrillError(f"task {upid} finished with exitstatus {status.get('exitstatus')!r}")
            return status
        if time.monotonic() >= deadline:
            raise DrillError(f"task {upid} did not finish within {timeout:.0f}s")
        sleep(poll)


def guest_agent(client: PveClient, node: str, vmid: int, command: str):
    return client.post(f"/nodes/{node}/qemu/{vmid}/agent", {"command": command})


def wait_agent(client: PveClient, node: str, vmid: int, *, timeout: float, poll: float = 4.0,
               sleep=time.sleep) -> float:
    """Wait until the guest agent answers ``ping``; return the elapsed seconds."""
    started = time.monotonic()
    deadline = started + timeout
    while True:
        try:
            guest_agent(client, node, vmid, "ping")
            return time.monotonic() - started
        except DrillError:
            if time.monotonic() >= deadline:
                raise DrillError(f"guest agent did not answer within {timeout:.0f}s") from None
            sleep(poll)


def _read_vmid_config(client: PveClient, node: str, vmid: int) -> dict:
    config = client.get(f"/nodes/{node}/qemu/{vmid}/config")
    if not isinstance(config, dict):
        raise DrillError(f"no config for VM {vmid}")
    return config


def preflight(client: PveClient, node: str, storage: str, vmid: int, archive: str,
              source_vmid: int | None) -> dict:
    """Read-only checks; returns context for the plan/execution."""
    validate_archive(archive)
    archive_storage = archive.split(":", 1)[0]
    vms = client.get(f"/nodes/{node}/qemu") or []
    used = {entry.get("vmid") for entry in vms if isinstance(entry, dict)}
    check_target(vmid, used)
    storages = client.get(f"/nodes/{node}/storage") or []
    by_name = {entry.get("storage"): entry for entry in storages if isinstance(entry, dict)}
    target = by_name.get(storage)
    if target is None:
        raise SafetyRefused(f"storage {storage!r} does not exist on node {node!r}")
    if "images" not in (target.get("content") or ""):
        raise SafetyRefused(f"storage {storage!r} cannot hold VM images")
    if not target.get("active"):
        raise SafetyRefused(f"storage {storage!r} is not active")
    archive_target = by_name.get(archive_storage)
    if archive_target is None:
        raise SafetyRefused(f"archive storage {archive_storage!r} does not exist on node {node!r}")
    if not archive_target.get("active"):
        raise SafetyRefused(f"archive storage {archive_storage!r} is not active")
    backups = client.get(f"/nodes/{node}/storage/{archive_storage}/content?content=backup") or []
    volids = {entry.get("volid") for entry in backups if isinstance(entry, dict)}
    if archive not in volids:
        raise SafetyRefused(f"archive {archive!r} is not present on storage {archive_storage!r}")
    context = {"used_vmids": used, "source_config": None, "source_agent": None}
    if source_vmid is not None:
        context["source_config"] = _read_vmid_config(client, node, source_vmid)
        try:
            context["source_agent"] = guest_agent(client, node, source_vmid, "get-osinfo")
        except DrillError:
            context["source_agent"] = None
    return context


def plan_drill(node: str, storage: str, vmid: int, archive: str, *, boot: bool) -> list[dict]:
    steps = [
        {"step": "restore", "method": "POST", "path": f"/nodes/{node}/qemu",
         "params": {"archive": archive, "vmid": vmid, "storage": storage, "unique": 1}},
    ]
    if boot:
        steps.append({"step": "isolate", "method": "PUT", "path": f"/nodes/{node}/qemu/{vmid}/config",
                      "params": {"net0": "<clone net0 with link_down=1>", "onboot": 0}})
        steps.append({"step": "start", "method": "POST", "path": f"/nodes/{node}/qemu/{vmid}/status/start",
                      "params": {}})
        steps.append({"step": "verify-agent", "method": "POST",
                      "path": f"/nodes/{node}/qemu/{vmid}/agent", "params": {"command": "get-osinfo"}})
    steps.append({"step": "cleanup", "method": "DELETE",
                  "path": f"/nodes/{node}/qemu/{vmid}?purge=1&destroy-unreferenced-disks=1",
                  "params": {}})
    return steps


def survey(client: PveClient, node: str, storage: str, vmid: int, context: dict) -> dict:
    """Verification summary after the clone has booted."""
    config = _read_vmid_config(client, node, vmid)
    summary: dict = {
        "config": {key: config.get(key) for key in ("name", "cores", "memory", "onboot", "net0")},
        "disk_sizes": disk_sizes(config),
        "agent": {},
        "checks": {},
    }
    for command, key in (("get-osinfo", "osinfo"), ("get-host-name", "hostname"),
                         ("get-fsinfo", "fsinfo")):
        try:
            summary["agent"][key] = guest_agent(client, node, vmid, command).get("result")
        except DrillError as error:
            summary["agent"][key] = {"error": str(error)}
    sizes = summary["disk_sizes"]
    source_config = context.get("source_config")
    checks = summary["checks"]
    if source_config:
        source_sizes = disk_sizes(source_config)
        common = set(source_sizes) & set(sizes)
        checks["disk_sizes_match_source"] = bool(common) and all(
            sizes[key] == source_sizes[key] for key in common
        )
        checks["source_disks_not_restored"] = sorted(set(source_sizes) - set(sizes))
        checks["cores_match_source"] = str(config.get("cores")) == str(source_config.get("cores"))
        checks["memory_match_source"] = str(config.get("memory")) == str(source_config.get("memory"))
    root = next(
        (entry for entry in (summary["agent"].get("fsinfo") or [])
         if isinstance(entry, dict) and entry.get("mountpoint") == "/"),
        None,
    )
    checks["root_filesystem_present"] = bool(root)
    if root and isinstance(root, dict):
        checks["root_total_bytes"] = root.get("total-bytes")
        checks["root_used_bytes"] = root.get("used-bytes")
    checks["hostname_present"] = bool(summary["agent"].get("hostname", {}).get("host-name")) \
        if isinstance(summary["agent"].get("hostname"), dict) else False
    summary["ok"] = all(value for key, value in checks.items() if isinstance(value, bool))
    return summary


def _stop_and_destroy(client: PveClient, node: str, storage: str, vmid: int, *, timeout: float,
                      shutdown_timeout: float = 60.0, poll: float = 2.0, sleep=time.sleep) -> dict:
    """Shut the clone down (bounded ACPI attempt, then hard stop) and destroy it."""
    result: dict = {}
    current = client.get(f"/nodes/{node}/qemu/{vmid}/status/current") or {}
    if current.get("status") != "stopped":
        try:
            upid = client.post(f"/nodes/{node}/qemu/{vmid}/status/shutdown")
            wait_task(client, node, upid, timeout=shutdown_timeout, poll=poll, sleep=sleep)
        except DrillError as error:
            result["shutdown_error"] = str(error)
    deadline = time.monotonic() + shutdown_timeout
    while True:
        current = client.get(f"/nodes/{node}/qemu/{vmid}/status/current") or {}
        if current.get("status") == "stopped":
            break
        if time.monotonic() >= deadline:
            result["hard_stopped"] = True
            upid = client.post(f"/nodes/{node}/qemu/{vmid}/status/stop")
            wait_task(client, node, upid, timeout=timeout, poll=poll, sleep=sleep)
            break
        sleep(poll)
    upid = client.delete(f"/nodes/{node}/qemu/{vmid}?purge=1&destroy-unreferenced-disks=1")
    wait_task(client, node, upid, timeout=timeout, poll=poll, sleep=sleep)
    result["vmid_absent"] = not any(
        entry.get("vmid") == vmid for entry in (client.get("/nodes/" + node + "/qemu") or [])
    )
    result["volumes_remaining"] = [
        entry.get("volid")
        for entry in (client.get(f"/nodes/{node}/storage/{storage}/content") or [])
        if str(vmid) in str(entry.get("volid", ""))
    ]
    return result


def run_drill(options: argparse.Namespace, *, client: PveClient, sleep=time.sleep) -> dict:
    """Execute (or plan) one restore drill; returns a JSON-serialisable report."""
    if options.archive:
        archive = validate_archive(options.archive)
        archive_origin = "explicit"
    else:
        if options.latest_archive_for is None:
            raise SafetyRefused("either --archive or --latest-archive-for is required")
        archive = resolve_archive(client, options.node, options.archive_storage, options.latest_archive_for)
        archive_origin = f"latest-for-vmid-{options.latest_archive_for}"
    context = preflight(client, options.node, options.storage, options.vmid, archive, options.source_vmid)
    report: dict = {
        "archive": archive,
        "archive_origin": archive_origin,
        "vmid": options.vmid,
        "storage": options.storage,
        "node": options.node,
        "boot": bool(options.boot),
        "execute": bool(options.execute),
        "steps": [],
    }
    if options.source_vmid is not None:
        source = context.get("source_config") or {}
        report["source"] = {"vmid": options.source_vmid, "name": source.get("name")}
    if not options.execute:
        report["plan"] = plan_drill(options.node, options.storage, options.vmid, archive, boot=options.boot)
        report["preflight"] = {"archive_present": True, "vmid_free": True}
        return report

    started = time.monotonic()
    restored = False
    try:
        upid = client.post("/nodes/" + options.node + "/qemu", {
            "archive": archive, "vmid": options.vmid, "storage": options.storage, "unique": 1,
        })
        wait_task(client, options.node, upid, timeout=options.timeout, sleep=sleep)
        restored = True
        report["steps"].append({"step": "restore", "ok": True, "upid": upid,
                                "seconds": round(time.monotonic() - started, 1)})
        report["restored_config"] = _read_vmid_config(client, options.node, options.vmid)
        if options.boot:
            config = report["restored_config"]
            net0 = config.get("net0")
            isolate = {"onboot": 0}
            if net0:
                isolate["net0"] = force_link_down(net0)
            client.put(f"/nodes/{options.node}/qemu/{options.vmid}/config", isolate)
            boot_started = time.monotonic()
            upid = client.post(f"/nodes/{options.node}/qemu/{options.vmid}/status/start")
            wait_task(client, options.node, upid, timeout=options.timeout, sleep=sleep)
            agent_seconds = wait_agent(client, options.node, options.vmid,
                                       timeout=options.agent_timeout, sleep=sleep)
            report["steps"].append({
                "step": "boot-verify", "ok": True,
                "boot_seconds": round(time.monotonic() - boot_started - agent_seconds, 1),
                "agent_seconds": round(agent_seconds, 1),
                "isolated": bool(net0),
            })
            report["verification"] = survey(client, options.node, options.storage, options.vmid, context)
    except DrillError as error:
        report["error"] = str(error)
    finally:
        if restored and not options.keep:
            try:
                cleanup = _stop_and_destroy(
                    client, options.node, options.storage, options.vmid,
                    timeout=options.timeout, shutdown_timeout=options.shutdown_timeout, sleep=sleep,
                )
                report["cleanup"] = cleanup
            except DrillError as error:
                report["cleanup"] = {"error": str(error)}
    report["seconds"] = round(time.monotonic() - started, 1)
    report["ok"] = (
        "error" not in report
        and bool(report.get("cleanup", {}).get("vmid_absent"))
        and not report.get("cleanup", {}).get("error")
    )
    if report.get("verification") is not None:
        report["ok"] = report["ok"] and bool(report["verification"].get("ok"))
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated PVE VM restore drill (dry run by default).")
    parser.add_argument("--node", default="pve", help="PVE node name (default: pve)")
    parser.add_argument("--endpoint", default="https://192.168.0.10:8006", help="PVE API endpoint")
    parser.add_argument("--token-file", default="~/.config/devagent/pve-api-token",
                        help="file with one 'tokenid=secret' PVE API token (mode 0600)")
    parser.add_argument("--ca-file", default="", help="CA bundle for the PVE endpoint (optional)")
    parser.add_argument("--insecure", action="store_true", help="accept the self-signed PVE certificate")
    parser.add_argument("--archive", default="", help="backup volume id, e.g. storage:backup/file.vma.zst")
    parser.add_argument("--latest-archive-for", type=int, default=None,
                        help="auto-select the newest vzdump archive of this production VMID")
    parser.add_argument("--archive-storage", default="xeonolab-backup",
                        help="storage holding the vzdump archives for --latest-archive-for")
    parser.add_argument("--vmid", type=int, required=True, help="scratch VMID (112..989)")
    parser.add_argument("--storage", default="local-lvm", help="target image storage (default: local-lvm)")
    parser.add_argument("--source-vmid", type=int, default=None,
                        help="optional production VMID to compare config against (read-only)")
    parser.add_argument("--no-boot", action="store_true", help="stop after restore (no offline boot)")
    parser.add_argument("--keep", action="store_true", help="leave the scratch VM in place for inspection")
    parser.add_argument("--execute", action="store_true", help="perform the drill (default: dry run)")
    parser.add_argument("--timeout", type=float, default=900.0, help="per-task timeout in seconds")
    parser.add_argument("--agent-timeout", type=float, default=300.0,
                        help="guest-agent boot timeout in seconds")
    parser.add_argument("--shutdown-timeout", type=float, default=60.0,
                        help="seconds to wait for ACPI shutdown before hard stop (default: 60)")
    options = parser.parse_args(argv)
    if bool(options.archive) == (options.latest_archive_for is not None):
        parser.error("exactly one of --archive or --latest-archive-for is required")
    options.boot = not options.no_boot
    return options


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        token = read_token(options.token_file)
        client = PveClient(options.endpoint, token, ca_file=options.ca_file, insecure=options.insecure)
        report = run_drill(options, client=client)
    except SafetyRefused as error:
        print(json.dumps({"ok": False, "refused": str(error)}, ensure_ascii=False))
        return 2
    except DrillError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not options.execute:
        return 0
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
