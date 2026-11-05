#!/usr/bin/python3
"""Summarise the public/LAN sampling JSONL log (issue #126, read-only).

`scripts/public_probe.py` writes one JSON report per round to
``/var/lib/devagent/logs/public-probe.jsonl`` (via the 10-minute timer). This
tool aggregates those rounds so the maintenance loop can quote success rates,
latency and concrete failure windows instead of single curls:

    uv run python scripts/public_probe_report.py --since-hours 24

Exit code 0 = report produced, 1 = no valid rounds, 2 = log missing.
With ``--fail-on-errors`` the exit code is 1 when any sample failed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import statistics

DEFAULT_LOG = "/var/lib/devagent/logs/public-probe.jsonl"


def parse_timestamp(value: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except (TypeError, ValueError):
        return None


def load_rounds(path: str, *, since_hours: float | None = None,
                now: datetime.datetime | None = None) -> tuple[list[dict], int]:
    """Read JSONL rounds; return (valid rounds, invalid line count)."""
    log = pathlib.Path(path)
    if not log.is_file():
        raise FileNotFoundError(path)
    rounds: list[dict] = []
    invalid = 0
    cutoff = None
    if since_hours is not None:
        reference = now or datetime.datetime.now(datetime.timezone.utc)
        cutoff = reference - datetime.timedelta(hours=since_hours)
    for line in log.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            round_data = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if not isinstance(round_data, dict) or not isinstance(round_data.get("entries"), list):
            invalid += 1
            continue
        started = parse_timestamp(str(round_data.get("started_at", "")))
        if cutoff is not None and (started is None or started < cutoff):
            continue
        rounds.append(round_data)
    return rounds, invalid


def summarise(rounds: list[dict]) -> dict:
    """Aggregate parsed rounds per URL."""
    urls: dict[str, dict] = {}
    for round_data in rounds:
        started = round_data.get("started_at")
        for entry in round_data.get("entries") or []:
            if not isinstance(entry, dict) or not entry.get("url"):
                continue
            aggregate = urls.setdefault(entry["url"], {
                "url": entry["url"],
                "rounds": 0,
                "samples": 0,
                "successes": 0,
                "failures": 0,
                "status_counts": {},
                "round_medians_ms": [],
                "worst_p95_ms": 0.0,
                "failure_rounds": [],
            })
            aggregate["rounds"] += 1
            aggregate["samples"] += int(entry.get("samples") or 0)
            aggregate["successes"] += int(entry.get("successes") or 0)
            aggregate["failures"] += len(entry.get("failures") or [])
            for code, count in (entry.get("status_counts") or {}).items():
                aggregate["status_counts"][code] = aggregate["status_counts"].get(code, 0) + int(count)
            median = entry.get("median_ms")
            if isinstance(median, (int, float)):
                aggregate["round_medians_ms"].append(float(median))
            p95 = entry.get("p95_ms")
            if isinstance(p95, (int, float)):
                aggregate["worst_p95_ms"] = max(aggregate["worst_p95_ms"], float(p95))
            if entry.get("failures"):
                aggregate["failure_rounds"].append({
                    "started_at": started,
                    "samples": entry.get("samples"),
                    "successes": entry.get("successes"),
                    "failures": entry["failures"][:3],
                })
    entries = []
    for aggregate in urls.values():
        medians = aggregate.pop("round_medians_ms")
        aggregate["median_of_round_medians_ms"] = round(statistics.median(medians), 1) if medians else 0.0
        aggregate["success_rate"] = round(
            aggregate["successes"] / aggregate["samples"], 4
        ) if aggregate["samples"] else 0.0
        aggregate["ok"] = aggregate["failures"] == 0 and aggregate["samples"] > 0
        entries.append(aggregate)
    entries.sort(key=lambda item: item["url"])
    started = [parse_timestamp(str(round_data.get("started_at", ""))) for round_data in rounds]
    started = [value for value in started if value is not None]
    return {
        "rounds": len(rounds),
        "window": {
            "from": min(started).isoformat().replace("+00:00", "Z") if started else None,
            "to": max(started).isoformat().replace("+00:00", "Z") if started else None,
        },
        "entries": entries,
        "ok": all(entry["ok"] for entry in entries) and bool(entries),
    }


def format_human(report: dict) -> str:
    lines = [
        f"rounds: {report['rounds']} | window: {report['window']['from'] or '-'} .. "
        f"{report['window']['to'] or '-'} | all-green: {report['ok']}"
    ]
    for entry in report["entries"]:
        statuses = ", ".join(f"{code}x{count}" for code, count in sorted(entry["status_counts"].items()))
        lines.append(
            f"{entry['url']} -> {entry['successes']}/{entry['samples']} ok "
            f"({entry['success_rate'] * 100:.1f}%) | statuses: {statuses or '-'} | "
            f"median {entry['median_of_round_medians_ms']:.0f} ms | worst p95 "
            f"{entry['worst_p95_ms']:.0f} ms | failed rounds: {len(entry['failure_rounds'])}"
        )
        for failure in entry["failure_rounds"][:5]:
            first = failure["failures"][0] if failure["failures"] else {}
            detail = first.get("error") or (
                f"HTTP {first.get('status')}" if first.get("status") else "unknown failure"
            )
            lines.append(f"    {failure['started_at']}: {failure['successes']}/{failure['samples']} ok - {detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise the public probe JSONL log (read-only).")
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"JSONL log path (default: {DEFAULT_LOG})")
    parser.add_argument("--since-hours", type=float, default=None, help="only rounds newer than N hours")
    parser.add_argument("--json", action="store_true", help="print the aggregate report as JSON")
    parser.add_argument("--fail-on-errors", action="store_true",
                        help="exit 1 when any sample in the window failed")
    args = parser.parse_args(argv)
    try:
        rounds, invalid = load_rounds(args.log, since_hours=args.since_hours)
    except FileNotFoundError:
        print(f"log not found: {args.log}")
        return 2
    report = summarise(rounds)
    report["invalid_lines"] = invalid
    print(json.dumps(report, ensure_ascii=False) if args.json else format_human(report))
    if not rounds:
        return 1
    if args.fail_on_errors and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
