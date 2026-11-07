#!/usr/bin/python3
"""Read-only operational audit for the XeonoLab LAN targets (issue #391).

Runs a fixed, read-only command set over SSH and reports drift that the
maintenance loop must not miss: disk pressure, failed systemd units, release-
directory retention, scheduled timers, backup age, 24h error-log noise and TLS
certificate expiry. Never writes to a target and never reads credentials.

Usage (from the DevAgent host):

    uv run python scripts/ops_audit.py \\
      --hosts 192.168.0.100,192.168.0.101,192.168.0.104,192.168.0.109,192.168.0.110,192.168.0.111 \\
      --key /tmp/devagent-admin/vm-key \\
      --known-hosts /tmp/devagent-admin/known_hosts \\
      --cert 192.168.0.101:9443 --cert 192.168.0.101:9444 --cert 192.168.0.101:9445 \\
      --ca-file /etc/devagent/lan-root-ca.crt

Exit code 0 means no findings; 1 means at least one finding was reported.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request

# Hardware services that are expected to be inactive on these VMs; listed so a
# noisy-but-known unit does not hide a real failure.
DEFAULT_ALLOWED_FAILED = ("openipmi.service",)

REMOTE_COMMAND = r"""
set -u
echo '###hostname'; hostname 2>/dev/null || echo unknown
echo '###df'; df -Pk / 2>/dev/null | tail -1
echo '###failed'; sudo -n systemctl --failed --no-legend 2>/dev/null | head -20
echo '###timers'; systemctl list-timers --no-pager 2>/dev/null | grep -E 'xeonolab|devagent|backup' | head -20
echo '###release'; readlink /opt/xeonolab/current 2>/dev/null; ls -1 /opt/xeonolab/releases 2>/dev/null | wc -l
echo '###backup'; line=$(sudo -n find /var/backups/xeonolab -maxdepth 1 -mindepth 1 -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1); if [ -n "$line" ]; then set -- $line; echo "$2"; echo "${1%%.*}"; else echo none; fi
echo '###backupdomains'; for d in xelibrary forgejo keycloak desktop data; do p=/var/backups/xeonolab/$d; sudo -n test -d "$p" || continue; line=$(sudo -n find "$p" -maxdepth 1 -mindepth 1 -type d -name '[!.]*' -printf '%T@ %f\n' 2>/dev/null | sort -rn | head -1); if [ -n "$line" ]; then set -- $line; echo "$d $2 ${1%%.*}"; else echo "$d none 0"; fi; done
echo '###backupunits'; for u in xeonolab-xelibrary-backup.timer xeonolab-forgejo-backup.timer xeonolab-postgres-backup.timer xeonolab-desktop-backup.timer xeonolab-data-backup.timer xeonolab-xelibrary-backup.service xeonolab-forgejo-backup.service xeonolab-postgres-backup.service xeonolab-desktop-backup.service xeonolab-data-backup.service; do load=$(systemctl show -p LoadState --value "$u" 2>/dev/null); [ "$load" = "not-found" ] && continue; echo "$u $(systemctl is-active "$u" 2>/dev/null || true) $(systemctl is-enabled "$u" 2>/dev/null || true) $(systemctl show -p Result --value "$u" 2>/dev/null)"; done
echo '###monitoring'; for u in xeonolab-public-probe.timer xeonolab-pve-restore-drill.timer; do load=$(systemctl show -p LoadState --value "$u" 2>/dev/null); [ "$load" = "not-found" ] && continue; echo "$u $(systemctl is-active "$u" 2>/dev/null || true) $(systemctl is-enabled "$u" 2>/dev/null || true) $(systemctl show -p Result --value "$u" 2>/dev/null)"; done
for f in /var/lib/devagent/logs/public-probe.jsonl /var/lib/devagent/logs/pve-restore-drill.jsonl; do sudo -n test -f "$f" || continue; echo "$f|$(sudo -n stat -c %Y "$f" 2>/dev/null)|$(sudo -n sh -c "wc -l < \"$f\"" 2>/dev/null)"; done
drill_line=$(sudo -n tail -n 1 /var/lib/devagent/logs/pve-restore-drill.jsonl 2>/dev/null); [ -n "$drill_line" ] && echo "drill $drill_line"
echo '###cloudflared'; if systemctl show -p LoadState --value cloudflared.service 2>/dev/null | grep -q not-found; then echo absent; else echo "version $(/usr/bin/cloudflared --version 2>/dev/null | head -1 | cut -d' ' -f3)"; echo "active $(systemctl is-active cloudflared 2>/dev/null || true)"; echo "restarts $(systemctl show -p NRestarts --value cloudflared 2>/dev/null || echo 0)"; start=$(systemctl show -p ActiveEnterTimestamp --value cloudflared.service 2>/dev/null); echo "drops $(sudo -n journalctl -u cloudflared --since \"$start\" --no-pager 2>/dev/null | grep -c 'Lost connection with the edge')"; echo "ha $(sudo -n curl -s --max-time 5 http://127.0.0.1:20241/metrics 2>/dev/null | grep '^cloudflared_tunnel_ha_connections ' | tail -1 | awk '{print $2}')"; fi
echo '###errors'; sudo -n journalctl --since '24 hours ago' -p err --no-pager -o cat 2>/dev/null | sort | uniq -c | sort -rn | head -6
"""


def parse_host_output(text: str) -> dict:
    """Split the marker-delimited remote output into a report dict."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        match = re.fullmatch(r"###(\w+)", line.strip())
        if match:
            current = match.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    report: dict = {"hostname": None, "disk": None, "failed_units": [], "timers": [],
                    "release": None, "release_count": None, "backup_path": None,
                    "backup_age_hours": None, "backup_domains": {}, "backup_units": [],
                    "error_lines": []}
    if sections.get("hostname"):
        report["hostname"] = sections["hostname"][0].strip() or None
    if sections.get("df"):
        report["disk"] = parse_df_line(sections["df"][0])
    for line in sections.get("failed", []):
        unit = line.split()[1] if len(line.split()) > 1 else line.strip()
        if unit and unit.startswith("●"):
            unit = line.split()[2] if len(line.split()) > 2 else unit
        if unit:
            report["failed_units"].append(unit)
    for line in sections.get("timers", []):
        if line.strip():
            report["timers"].append(" ".join(line.split()))
    release_lines = [line.strip() for line in sections.get("release", []) if line.strip()]
    if release_lines:
        report["release"] = release_lines[0]
        numbers = [line for line in release_lines if line.isdigit()]
        report["release_count"] = int(numbers[0]) if numbers else None
    backup_lines = [line.strip() for line in sections.get("backup", []) if line.strip()]
    if backup_lines and backup_lines[0] != "none":
        report["backup_path"] = backup_lines[0]
        if len(backup_lines) > 1 and backup_lines[1].isdigit():
            age = datetime.datetime.now(datetime.UTC).timestamp() - int(backup_lines[1])
            report["backup_age_hours"] = round(age / 3600, 1)
    now = datetime.datetime.now(datetime.UTC).timestamp()
    for line in sections.get("backupdomains", []):
        parts = line.split()
        if len(parts) != 3 or parts[0] in report["backup_domains"]:
            continue
        domain, snapshot, stamp = parts
        entry = {"snapshot": None if snapshot == "none" else snapshot}
        if snapshot != "none" and stamp.isdigit():
            entry["age_hours"] = round((now - int(stamp)) / 3600, 1)
        report["backup_domains"][domain] = entry
    for line in sections.get("backupunits", []):
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        report["backup_units"].append({
            "name": name,
            "active": parts[1] if len(parts) > 1 else "",
            "enabled": parts[2] if len(parts) > 2 else "",
            "result": parts[3] if len(parts) > 3 else "",
        })
    monitoring_units = []
    monitoring_logs: dict[str, dict] = {}
    for line in sections.get("monitoring", []):
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped:
            parts = stripped.split("|")
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                monitoring_logs[parts[0]] = {"mtime": int(parts[1]), "lines": int(parts[2])}
            continue
        parts = stripped.split()
        if len(parts) >= 3 and parts[0].endswith(".timer"):
            monitoring_units.append({
                "name": parts[0],
                "active": parts[1],
                "enabled": parts[2],
                "result": parts[3] if len(parts) > 3 else "",
            })
    report["monitoring"] = {"units": monitoring_units, "logs": monitoring_logs}
    drill = None
    for line in sections.get("monitoring", []):
        stripped = line.strip()
        if not stripped.startswith("drill "):
            continue
        try:
            record = json.loads(stripped[len("drill "):])
        except ValueError:
            continue
        cleanup = record.get("cleanup") or {}
        drill = {
            "ok": bool(record.get("ok")),
            "archive": record.get("archive"),
            "archive_origin": record.get("archive_origin"),
            "seconds": record.get("seconds"),
            "vmid_absent": bool(cleanup.get("vmid_absent")),
            "volumes_remaining": len(cleanup.get("volumes_remaining") or []),
        }
    report["drill"] = drill
    cloudflared: dict = {"present": False}
    for line in sections.get("cloudflared", []):
        stripped = line.strip()
        if stripped == "absent":
            continue
        if " " not in stripped:
            continue
        key, value = stripped.split(" ", 1)
        if key == "version":
            cloudflared["version"] = value
        elif key == "active":
            cloudflared["active"] = value
        elif key == "restarts":
            cloudflared["restarts"] = int(value) if value.isdigit() else 0
        elif key == "drops":
            cloudflared["drops_since_start"] = int(value) if value.isdigit() else 0
        elif key == "ha":
            cloudflared["ha_connections"] = int(value) if value.isdigit() else 0
        if key in ("version", "active", "restarts", "drops", "ha"):
            cloudflared["present"] = True
    report["cloudflared"] = cloudflared
    report["error_lines"] = [line.strip() for line in sections.get("errors", []) if line.strip()]
    return report


def parse_df_line(line: str) -> dict | None:
    """Parse ``df -Pk /`` output: filesystem blocks used available capacity mount."""
    parts = line.split()
    if len(parts) < 6 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return {
        "filesystem": parts[0],
        "total_kib": int(parts[1]),
        "used_kib": int(parts[2]),
        "available_kib": int(parts[3]),
        "used_percent": int(parts[4].rstrip("%")) if parts[4].rstrip("%").isdigit() else None,
        "mount": parts[5],
    }


def pve_get(endpoint: str, token: str, path: str, *, insecure: bool = False,
            ca_file: str | None = None, timeout: float = 20.0) -> object:
    """GET one read-only PVE API path; the token is only sent in the header."""
    request = urllib.request.Request(
        endpoint.rstrip("/") + path,
        headers={"Authorization": "PVEAPIToken=" + token, "Accept": "application/json"},
    )
    if ca_file:
        context = ssl.create_default_context(cafile=ca_file)
    elif insecure:
        context = ssl._create_unverified_context()  # noqa: SLF001 - explicit opt-in for self-signed PVE certs
    else:
        context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        return json.loads(response.read().decode())["data"]


def pve_vm_backup_status(endpoint: str, token: str, *, insecure: bool = False,
                         ca_file: str | None = None) -> dict:
    """Read scheduled PVE backup jobs and the newest vzdump archive per VM."""
    try:
        jobs = pve_get(endpoint, token, "/api2/json/cluster/backup", insecure=insecure, ca_file=ca_file)
        nodes = pve_get(endpoint, token, "/api2/json/nodes", insecure=insecure, ca_file=ca_file)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        return {"error": f"PVE API unavailable: {error}"}
    node = nodes[0]["node"] if nodes else "pve"
    archives: dict[str, int] = {}
    for job in jobs or []:
        storage = job.get("storage")
        if not storage:
            continue
        try:
            content = pve_get(
                endpoint, token,
                f"/api2/json/nodes/{node}/storage/{storage}/content?content=backup",
                insecure=insecure, ca_file=ca_file,
            )
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            return {"error": f"PVE storage {storage} content unavailable: {error}", "jobs": jobs}
        for entry in content or []:
            match = re.search(r"/vzdump-qemu-(\d+)-", str(entry.get("volid", "")))
            if not match:
                continue
            key = f"{storage}:{int(match.group(1))}"
            archives[key] = max(archives.get(key, 0), int(entry.get("ctime") or 0))
    return {"jobs": jobs or [], "archives": archives}


def evaluate_pve_backups(status: dict, *, now: float, max_age_hours: float = 192.0) -> list[str]:
    """Return findings for the scheduled VM-level (vzdump) backups."""
    if status.get("error"):
        return [f"pve: {status['error']}"]
    findings: list[str] = []
    jobs = status.get("jobs") or []
    if not jobs:
        findings.append("pve: no scheduled VM backup jobs")
    archives = status.get("archives") or {}
    for job in jobs:
        label = job.get("id") or "job"
        if not job.get("enabled"):
            findings.append(f"pve: backup {label} is disabled")
        storage = job.get("storage") or "?"
        vmids = [int(item) for item in re.split(r"[,\s]+", str(job.get("vmid") or "")) if item.isdigit()]
        for vmid in vmids:
            created = archives.get(f"{storage}:{vmid}")
            if not created:
                findings.append(f"pve: VM{vmid} has no vzdump archive in {storage}")
                continue
            age = (now - created) / 3600
            if age > max_age_hours:
                findings.append(f"pve: VM{vmid} newest archive is {age:.0f}h old (> {max_age_hours:.0f}h)")
    return findings


def certificate_days_left(host: str, port: int, ca_file: str | None, timeout: float = 8.0) -> int | None:
    """Return whole days until the presented certificate expires."""
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                not_after = tls.getpeercert()["notAfter"]
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None
    expires = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
    return (expires - datetime.datetime.now(datetime.UTC).replace(tzinfo=None)).days


def evaluate_cloudflared(state: dict, name: str, *, max_drops_24h: int = 12) -> list[str]:
    """Flag a down or flapping public tunnel connector (issue #67).

    Drop counting starts at the current connector's start time so a previous
    version's incidents cannot mask or inflate the post-upgrade baseline.
    """
    if not state.get("present"):
        return []
    findings: list[str] = []
    if state.get("active") != "active":
        findings.append(f"{name}: cloudflared is {state.get('active') or 'unknown'}")
    if int(state.get("ha_connections") or 0) < 1:
        findings.append(f"{name}: cloudflared has no active HA connections")
    drops = int(state.get("drops_since_start") or 0)
    if max_drops_24h >= 0 and drops > max_drops_24h:
        findings.append(
            f"{name}: cloudflared lost the edge {drops} times since start (> {max_drops_24h}; issue #67)"
        )
    return findings


MONITORING_TIMERS = ("xeonolab-public-probe.timer", "xeonolab-pve-restore-drill.timer")
PROBE_LOG = "/var/lib/devagent/logs/public-probe.jsonl"
DRILL_LOG = "/var/lib/devagent/logs/pve-restore-drill.jsonl"


def evaluate_monitoring(monitoring: dict, name: str, *, max_probe_age_hours: float = 2.0,
                        max_restore_drill_age_days: float = 45.0,
                        now: float | None = None) -> list[str]:
    """Check that the maintenance-host sampling and restore-drill timers keep running."""
    findings: list[str] = []
    units = {unit.get("name"): unit for unit in monitoring.get("units") or []}
    for expected in MONITORING_TIMERS:
        unit = units.get(expected)
        if unit is None:
            findings.append(f"{name}: monitoring timer {expected} is missing")
            continue
        if unit.get("active") not in ("active", "activating"):
            findings.append(f"{name}: monitoring timer {expected} is {unit.get('active') or 'unknown'}")
        if unit.get("enabled") not in ("enabled", "enabled-runtime"):
            findings.append(f"{name}: monitoring timer {expected} is {unit.get('enabled') or 'unknown'}")
    logs = monitoring.get("logs") or {}
    current = now if now is not None else datetime.datetime.now(datetime.UTC).timestamp()
    probe = logs.get(PROBE_LOG)
    if probe is None:
        findings.append(f"{name}: public probe log is missing ({PROBE_LOG})")
    elif current - probe["mtime"] > max_probe_age_hours * 3600:
        findings.append(
            f"{name}: public probe log is {(current - probe['mtime']) / 3600:.1f}h old "
            f"(> {max_probe_age_hours:.0f}h)"
        )
    drill = logs.get(DRILL_LOG)
    if drill is None:
        findings.append(f"{name}: restore drill log is missing ({DRILL_LOG})")
    elif current - drill["mtime"] > max_restore_drill_age_days * 86400:
        findings.append(
            f"{name}: restore drill log is {(current - drill['mtime']) / 86400:.0f}d old "
            f"(> {max_restore_drill_age_days:.0f}d)"
        )
    record = monitoring.get("drill")
    if isinstance(record, dict):
        if not record.get("ok"):
            findings.append(f"{name}: last restore drill failed ({record.get('archive') or 'unknown archive'})")
        if record.get("vmid_absent") is not True or record.get("volumes_remaining"):
            findings.append(
                f"{name}: last restore drill cleanup left a VMID or volumes "
                f"(vmid_absent={record.get('vmid_absent')}, volumes={record.get('volumes_remaining')})"
            )
    return findings


def evaluate(reports: dict[str, dict], certificates: dict[str, int | None], *,
             min_free_mib: int = 5120, max_used_percent: int = 90, keep_releases: int = 4,
             release_tolerance: int = 2, max_backup_age_hours: float = 36.0,
             allowed_failed: tuple[str, ...] = DEFAULT_ALLOWED_FAILED,
             min_cert_days: int = 0, max_probe_age_hours: float = 2.0,
             max_restore_drill_age_days: float = 45.0,
             max_cloudflared_drops: int = 12) -> list[str]:
    """Return human-readable findings; an empty list means the fleet looks healthy."""
    findings: list[str] = []
    for host, report in sorted(reports.items()):
        name = report.get("hostname") or host
        disk = report.get("disk")
        if disk is None:
            findings.append(f"{name}: disk usage unavailable")
        else:
            if disk["used_percent"] is not None and disk["used_percent"] >= max_used_percent:
                findings.append(f"{name}: root filesystem {disk['used_percent']}% used (>= {max_used_percent}%)")
            if disk["available_kib"] is not None and disk["available_kib"] < min_free_mib * 1024:
                findings.append(f"{name}: only {disk['available_kib'] // 1024} MiB free on / (< {min_free_mib} MiB)")
        unexpected = [unit for unit in report.get("failed_units", []) if unit not in allowed_failed]
        if unexpected:
            findings.append(f"{name}: failed units: {', '.join(sorted(unexpected))}")
        count = report.get("release_count")
        # Deployments can transiently add one release directory per rollout; only
        # flag growth that the daily prune timer should already have removed.
        if count is not None and count > keep_releases + release_tolerance:
            findings.append(f"{name}: {count} release directories (retention keeps {keep_releases})")
        # Only hosts that actually carry a scheduled backup domain (XeLibrary,
        # Forgejo, Keycloak, desktop) are checked for freshness; one-off manual
        # archive directories on other hosts are informational only.
        for domain, entry in sorted((report.get("backup_domains") or {}).items()):
            if entry.get("snapshot") is None:
                findings.append(f"{name}: {domain} backup directory has no snapshot")
            elif entry.get("age_hours") is not None and entry["age_hours"] > max_backup_age_hours:
                findings.append(
                    f"{name}: {domain} backup is {entry['age_hours']:.0f}h old (> {max_backup_age_hours:.0f}h)"
                )
        findings.extend(evaluate_cloudflared(report.get("cloudflared") or {}, name,
                                              max_drops_24h=max_cloudflared_drops))
        if name == "dev-agent":
            findings.extend(evaluate_monitoring(
                report.get("monitoring") or {}, name,
                max_probe_age_hours=max_probe_age_hours,
                max_restore_drill_age_days=max_restore_drill_age_days,
            ))
        for unit in report.get("backup_units", []):
            if unit.get("enabled") == "not-found":
                continue
            if unit["name"].endswith(".timer") and unit.get("active") not in ("active", "activating"):
                findings.append(f"{name}: backup timer {unit['name']} is {unit.get('active') or 'unknown'}")
            if unit["name"].endswith(".service") and unit.get("result") == "failed":
                findings.append(f"{name}: backup unit {unit['name']} last run failed")
    for endpoint, days in sorted(certificates.items()):
        if days is None:
            findings.append(f"{endpoint}: certificate expiry unavailable")
        elif days < min_cert_days:
            findings.append(f"{endpoint}: certificate expires in {days} day(s) (< {min_cert_days})")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only XeonoLab fleet audit (issue #391).")
    parser.add_argument("--hosts", default="", help="comma-separated target hosts/IPs")
    parser.add_argument("--user", default="xenon")
    parser.add_argument("--key", default="", help="SSH private key path (0600)")
    parser.add_argument("--known-hosts", default="", help="SSH known_hosts path")
    parser.add_argument("--cert", action="append", default=[], metavar="HOST:PORT",
                        help="TLS endpoint to check (repeatable)")
    parser.add_argument("--ca-file", default="", help="CA bundle for the TLS endpoints")
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    parser.add_argument("--min-free-mib", type=int, default=5120)
    parser.add_argument("--max-used-percent", type=int, default=90)
    parser.add_argument("--keep-releases", type=int, default=4)
    parser.add_argument("--release-tolerance", type=int, default=2,
                        help="extra release directories tolerated between deploy and prune")
    parser.add_argument("--max-backup-age-hours", type=float, default=36.0,
                        help="maximum age of the newest snapshot per backup domain")
    parser.add_argument("--max-probe-age-hours", type=float, default=2.0,
                        help="maximum age of the public-probe log on dev-agent (default: 2)")
    parser.add_argument("--max-restore-drill-age-days", type=float, default=45.0,
                        help="maximum age of the restore-drill log on dev-agent (default: 45)")
    parser.add_argument("--max-cloudflared-drops", type=int, default=12,
                        help="maximum cloudflared edge drops since the current start before a finding (default: 12)")
    parser.add_argument("--allow-failed", action="append", default=None,
                        help="failed unit name to tolerate (repeatable; default openipmi.service)")
    parser.add_argument("--pve-token-file", default="",
                        help="file holding one PVE API token (tokenid=secret) for read-only VM backup checks")
    parser.add_argument("--pve-endpoint", default="https://192.168.0.10:8006")
    parser.add_argument("--pve-ca-file", default="", help="CA bundle for the PVE API (default: system trust)")
    parser.add_argument("--pve-insecure", action="store_true",
                        help="accept the self-signed PVE certificate (explicit opt-in)")
    parser.add_argument("--max-vm-backup-age-hours", type=float, default=192.0,
                        help="maximum age of the newest vzdump archive per scheduled VM")
    args = parser.parse_args(argv)

    allowed_failed = tuple(args.allow_failed) if args.allow_failed is not None else DEFAULT_ALLOWED_FAILED
    reports: dict[str, dict] = {}
    for host in [item.strip() for item in args.hosts.split(",") if item.strip()]:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                   "-o", "StrictHostKeyChecking=yes" if args.known_hosts else "StrictHostKeyChecking=no"]
        if args.known_hosts:
            command += ["-o", f"UserKnownHostsFile={args.known_hosts}"]
        if args.key:
            command += ["-i", args.key]
        command += [f"{args.user}@{host}", REMOTE_COMMAND]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            reports[host] = {"hostname": None, "error": str(error), "failed_units": [],
                             "timers": [], "release": None, "release_count": None,
                             "backup_path": None, "backup_age_hours": None,
                             "disk": None, "error_lines": []}
            continue
        reports[host] = parse_host_output(result.stdout)

    certificates: dict[str, int | None] = {}
    for endpoint in args.cert:
        host, _, port = endpoint.rpartition(":")
        certificates[endpoint] = certificate_days_left(host, int(port), args.ca_file or None)

    pve_status: dict | None = None
    pve_findings: list[str] = []
    if args.pve_token_file:
        try:
            token = pathlib.Path(args.pve_token_file).read_text().strip()
        except OSError as error:
            pve_status = {"error": f"cannot read PVE token file: {error}"}
            token = ""
        if token:
            pve_status = pve_vm_backup_status(
                args.pve_endpoint, token,
                insecure=args.pve_insecure, ca_file=args.pve_ca_file or None,
            )
        pve_findings = evaluate_pve_backups(
            pve_status or {}, now=time.time(), max_age_hours=args.max_vm_backup_age_hours
        )

    findings = evaluate(reports, certificates, min_free_mib=args.min_free_mib,
                        max_used_percent=args.max_used_percent, keep_releases=args.keep_releases,
                        release_tolerance=args.release_tolerance,
                        max_backup_age_hours=args.max_backup_age_hours,
                        allowed_failed=allowed_failed,
                        max_probe_age_hours=args.max_probe_age_hours,
                        max_restore_drill_age_days=args.max_restore_drill_age_days,
                        max_cloudflared_drops=args.max_cloudflared_drops)
    findings.extend(pve_findings)
    if args.json:
        print(json.dumps({"reports": reports, "certificates": certificates,
                          "pve_backups": pve_status, "findings": findings},
                         indent=2, ensure_ascii=False))
    else:
        for host, report in sorted(reports.items()):
            name = report.get("hostname") or host
            disk = report.get("disk") or {}
            domains = report.get("backup_domains") or {}
            backup = ", ".join(
                f"{domain}={entry.get('snapshot') or 'none'}"
                + (f" ({entry['age_hours']:.0f}h)" if entry.get("age_hours") is not None else "")
                for domain, entry in sorted(domains.items())
            ) or str(report.get("backup_path"))
            print(f"{name}: {disk.get('used_percent')}% used, {disk.get('available_kib', 0) // 1024} MiB free, "
                  f"{report.get('release_count')} releases, backup={backup}")
        for endpoint, days in sorted(certificates.items()):
            print(f"{endpoint}: certificate days_left={days}")
        print("findings:" if findings else "findings: none")
        for finding in findings:
            print(f"  - {finding}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
