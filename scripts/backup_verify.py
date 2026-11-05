#!/usr/bin/python3
"""Read-only re-verification of the newest nightly backup per data domain (issue #390).

The backup timers already verify each snapshot before publishing it; this tool
re-runs the same domain-specific `verify` commands against the **newest**
snapshot on every host so bit-rot or a broken install is detected between
snapshot windows. It never writes to a target, never reads credentials and uses
only fixed commands.

Domains discovered under ``/var/backups/xeonolab/<domain>``:

* ``xelibrary`` → ``<release>/scripts/xelibrary_backup_online.py verify --root``
* ``desktop``   → ``<release>/scripts/desktop_backup_online.py verify --dir <latest>``
* ``data``      → release tool, or the standalone tree on non-release hosts
* ``forgejo``   → ``/usr/local/libexec/xeonolab-forgejo-backup verify --dir <latest>``
* ``keycloak``  → ``/usr/local/libexec/xeonolab-postgres-backup verify --dir <latest>``

Usage (from the DevAgent host):

    uv run python scripts/backup_verify.py \\
      --hosts 192.168.0.100,192.168.0.101,192.168.0.103,192.168.0.104,192.168.0.105,192.168.0.106,192.168.0.107,192.168.0.109,192.168.0.110,192.168.0.111 \\
      --key /tmp/devagent-admin/vm-key --known-hosts /tmp/devagent-admin/known_hosts

Exit code 0 = every discovered newest snapshot verified, 1 = at least one
failure, 2 = no snapshot found anywhere.
"""

from __future__ import annotations

import argparse
import json
import subprocess

DOMAINS = ("xelibrary", "forgejo", "keycloak", "desktop", "data")

# Fixed remote script: discovers the domains present on this host and re-runs the
# same verify tool that produced the snapshot. No user-supplied fragments.
REMOTE_COMMAND = r"""
set -u
echo '###hostname'; hostname 2>/dev/null || echo unknown
echo '###backupverify'
for d in xelibrary forgejo keycloak desktop data; do
  p=/var/backups/xeonolab/$d
  sudo -n test -d "$p" || continue
  latest=$(sudo -n find "$p" -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
  if [ -z "$latest" ]; then
    echo "$d||fail: no snapshot directory"
    continue
  fi
  release=/opt/xeonolab/current
  out=''
  rc=0
  case "$d" in
    xelibrary)
      out=$(sudo -n "$release/.venv/bin/python" "$release/scripts/xelibrary_backup_online.py" verify --root "$p" 2>&1) || rc=$? ;;
    desktop)
      out=$(sudo -n "$release/.venv/bin/python" "$release/scripts/desktop_backup_online.py" verify --dir "$latest" 2>&1) || rc=$? ;;
    data)
      if sudo -n test -x "$release/.venv/bin/python"; then
        out=$(sudo -n "$release/.venv/bin/python" "$release/scripts/data_backup_online.py" verify --dir "$latest" 2>&1) || rc=$?
      else
        out=$(sudo -n PYTHONPATH=/usr/local/libexec/xeonolab-data-backup /usr/bin/python3 /usr/local/libexec/xeonolab-data-backup/scripts/data_backup_online.py verify --dir "$latest" 2>&1) || rc=$?
      fi ;;
    forgejo)
      out=$(sudo -n /usr/bin/python3 /usr/local/libexec/xeonolab-forgejo-backup verify --dir "$latest" 2>&1) || rc=$? ;;
    keycloak)
      out=$(sudo -n /usr/bin/python3 /usr/local/libexec/xeonolab-postgres-backup verify --dir "$latest" 2>&1) || rc=$? ;;
    *)
      out='unknown domain'; rc=2 ;;
  esac
  summary=$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-400)
  if [ "$rc" -eq 0 ]; then
    echo "$d|$(basename "$latest")|ok: $summary"
  else
    echo "$d|$(basename "$latest")|fail($rc): $summary"
  fi
done
"""


def parse_report(text: str) -> dict:
    """Split the marker-delimited remote output into a per-domain report."""
    hostname = "unknown"
    entries: list[dict] = []
    in_verify = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "###hostname":
            in_verify = False
            continue
        if stripped == "###backupverify":
            in_verify = True
            continue
        if not stripped:
            continue
        if not in_verify:
            if hostname == "unknown":
                hostname = stripped
            continue
        parts = stripped.split("|", 2)
        if len(parts) != 3:
            continue
        domain, snapshot, result = parts
        entries.append({
            "domain": domain,
            "snapshot": snapshot or None,
            "ok": result.startswith("ok:"),
            "detail": result,
        })
    return {"hostname": hostname, "entries": entries}


def verify_host(host: str, user: str, key: str, known_hosts: str, timeout: float = 120.0) -> dict:
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if known_hosts:
        command += ["-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}"]
    else:
        command += ["-o", "StrictHostKeyChecking=no"]
    if key:
        command += ["-i", key]
    command += [f"{user}@{host}", REMOTE_COMMAND]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"host": host, "hostname": "unknown", "error": str(error), "entries": []}
    if result.returncode != 0:
        return {"host": host, "hostname": "unknown", "error": (result.stderr or "").strip()[:400],
                "entries": []}
    report = parse_report(result.stdout)
    report["host"] = host
    if report["hostname"] == "unknown":
        report["hostname"] = host
    return report


def summarize(reports: list[dict]) -> dict:
    entries = [dict(entry, host=report["host"], hostname=report["hostname"])
               for report in reports for entry in report.get("entries", [])]
    failures = [entry for entry in entries if not entry["ok"]]
    errors = [report["host"] for report in reports if report.get("error")]
    return {
        "domains": len(entries),
        "ok": not failures and not errors and bool(entries),
        "failures": failures,
        "ssh_errors": errors,
        "entries": entries,
    }


def format_human(summary: dict, reports: list[dict]) -> str:
    lines: list[str] = []
    for report in reports:
        name = f"{report['host']} ({report['hostname']})"
        if report.get("error"):
            lines.append(f"{name}: SSH error: {report['error']}")
            continue
        if not report.get("entries"):
            lines.append(f"{name}: no backup domains")
            continue
        for entry in report["entries"]:
            mark = "ok" if entry["ok"] else "FAIL"
            lines.append(f"{name}: {entry['domain']} {entry['snapshot'] or '-'} -> {mark}: {entry['detail'][:200]}")
    if summary["failures"]:
        lines.append(f"failures: {len(summary['failures'])}")
    if summary["ssh_errors"]:
        lines.append(f"ssh errors: {', '.join(summary['ssh_errors'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-verify the newest nightly backup per domain (read-only).")
    parser.add_argument("--hosts", default="", help="comma-separated target hosts/IPs")
    parser.add_argument("--user", default="xenon")
    parser.add_argument("--key", default="", help="SSH private key path (0600)")
    parser.add_argument("--known-hosts", default="", help="SSH known_hosts path")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-host SSH timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    args = parser.parse_args(argv)
    hosts = [item.strip() for item in args.hosts.split(",") if item.strip()]
    if not hosts:
        parser.error("at least one host is required")
    reports = [verify_host(host, args.user, args.key, args.known_hosts, args.timeout) for host in hosts]
    summary = summarize(reports)
    payload = {"reports": reports, "summary": summary}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(format_human(summary, reports))
    if not summary["entries"]:
        return 2
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
