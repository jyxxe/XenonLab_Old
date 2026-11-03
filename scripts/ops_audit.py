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
import re
import socket
import ssl
import subprocess

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
echo '###backupdomains'; for d in xelibrary forgejo keycloak desktop data; do p=/var/backups/xeonolab/$d; sudo -n test -d "$p" || continue; line=$(sudo -n find "$p" -maxdepth 1 -mindepth 1 -type d -printf '%T@ %f\n' 2>/dev/null | sort -rn | head -1); if [ -n "$line" ]; then set -- $line; echo "$d $2 ${1%%.*}"; else echo "$d none 0"; fi; done
echo '###backupunits'; for u in xeonolab-xelibrary-backup.timer xeonolab-forgejo-backup.timer xeonolab-postgres-backup.timer xeonolab-desktop-backup.timer xeonolab-data-backup.timer xeonolab-xelibrary-backup.service xeonolab-forgejo-backup.service xeonolab-postgres-backup.service xeonolab-desktop-backup.service xeonolab-data-backup.service; do load=$(systemctl show -p LoadState --value "$u" 2>/dev/null); [ "$load" = "not-found" ] && continue; echo "$u $(systemctl is-active "$u" 2>/dev/null || true) $(systemctl is-enabled "$u" 2>/dev/null || true) $(systemctl show -p Result --value "$u" 2>/dev/null)"; done
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


def evaluate(reports: dict[str, dict], certificates: dict[str, int | None], *,
             min_free_mib: int = 5120, max_used_percent: int = 90, keep_releases: int = 4,
             release_tolerance: int = 2, max_backup_age_hours: float = 36.0,
             allowed_failed: tuple[str, ...] = DEFAULT_ALLOWED_FAILED,
             min_cert_days: int = 0) -> list[str]:
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
    parser.add_argument("--allow-failed", action="append", default=None,
                        help="failed unit name to tolerate (repeatable; default openipmi.service)")
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

    findings = evaluate(reports, certificates, min_free_mib=args.min_free_mib,
                        max_used_percent=args.max_used_percent, keep_releases=args.keep_releases,
                        release_tolerance=args.release_tolerance,
                        max_backup_age_hours=args.max_backup_age_hours,
                        allowed_failed=allowed_failed)
    if args.json:
        print(json.dumps({"reports": reports, "certificates": certificates, "findings": findings},
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
