#!/usr/bin/python3
"""Sample public and LAN entry points repeatedly (issue #126 tracking, read-only).

The maintenance loop needs evidence for the intermittent public Cloudflare
behaviour (#126/#67) instead of single anecdotal curls. This tool performs N
GET requests per URL, records status codes and latencies, and summarises
success ratios, status distribution and latency percentiles. It never follows
non-HTTP schemes, never sends credentials and never writes to any target.

Usage:

    uv run python scripts/public_probe.py \\
      --url https://xenon.jyxxe.com/ --url https://192.168.0.101/ \\
      --ca-file /etc/devagent/lan-root-ca.crt --samples 10 --interval 2

Exit code 0 = every sample succeeded (2xx/3xx), 1 = at least one failure.
"""

from __future__ import annotations

import argparse
import json
import math
import ssl
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 10.0


def probe(url: str, *, timeout: float = DEFAULT_TIMEOUT, ca_file: str = "") -> dict:
    """Perform one GET request and return {status, ms, ok, error?}."""
    context = ssl.create_default_context()
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "xeonolab-public-probe/1"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            response.read(2048)
            status = response.status
            error = None
    except urllib.error.HTTPError as failure:
        status = failure.code
        error = None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as failure:
        status = None
        error = str(failure)[:200]
    elapsed = round((time.monotonic() - started) * 1000, 1)
    ok = status is not None and 200 <= status < 400
    sample = {"status": status, "ms": elapsed, "ok": ok}
    if error:
        sample["error"] = error
    return sample


def classify(samples: list[dict]) -> dict:
    """Aggregate samples for one URL into counts and latency percentiles."""
    statuses: dict[str, int] = {}
    successes = 0
    latencies: list[float] = []
    failures: list[dict] = []
    for sample in samples:
        key = "error" if sample.get("status") is None else str(sample["status"])
        statuses[key] = statuses.get(key, 0) + 1
        latencies.append(sample["ms"])
        if sample["ok"]:
            successes += 1
        else:
            failures.append(sample)
    ordered = sorted(latencies)
    def percentile(fraction: float) -> float:
        if not ordered:
            return 0.0
        index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
        return ordered[index]
    return {
        "samples": len(samples),
        "successes": successes,
        "failures": failures,
        "status_counts": statuses,
        "median_ms": percentile(0.5),
        "p95_ms": percentile(0.95),
        "max_ms": ordered[-1] if ordered else 0.0,
        "ok": successes == len(samples) and bool(samples),
    }


def run(urls: list[str], *, samples: int, interval: float, timeout: float, ca_file: str,
        sleep=None, probe_fn=None) -> dict:
    """Probe every URL ``samples`` times and return the structured report."""
    sleep = time.sleep if sleep is None else sleep
    probe_fn = probe if probe_fn is None else probe_fn
    report: dict = {"urls": urls, "samples": samples, "interval": interval,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "entries": []}
    for index in range(samples):
        for url in urls:
            sample = probe_fn(url, timeout=timeout, ca_file=ca_file)
            entry = next((item for item in report["entries"] if item["url"] == url), None)
            if entry is None:
                entry = {"url": url, "samples": []}
                report["entries"].append(entry)
            entry["samples"].append(sample)
        if index + 1 < samples and interval > 0:
            sleep(interval)
    for entry in report["entries"]:
        entry.update(classify(entry.pop("samples")))
    report["ok"] = all(entry["ok"] for entry in report["entries"]) and bool(report["entries"])
    return report


def format_human(report: dict) -> str:
    lines: list[str] = []
    for entry in report["entries"]:
        mark = "ok" if entry["ok"] else "FAIL"
        statuses = ", ".join(f"{code}x{count}" for code, count in sorted(entry["status_counts"].items()))
        lines.append(
            f"{entry['url']} -> {mark}: {entry['successes']}/{entry['samples']} ok"
            f" | statuses: {statuses or '-'} | median {entry['median_ms']:.0f} ms"
            f" | p95 {entry['p95_ms']:.0f} ms | max {entry['max_ms']:.0f} ms"
        )
        for failure in entry["failures"][:3]:
            detail = failure.get("error") or f"HTTP {failure.get('status')}"
            lines.append(f"    failure: {detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repeatable public/LAN entry sampling (read-only).")
    parser.add_argument("--url", action="append", default=[], help="URL to sample (repeatable)")
    parser.add_argument("--samples", type=int, default=10, help="samples per URL (default: 10)")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between rounds (default: 2)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-request timeout in seconds")
    parser.add_argument("--ca-file", default="", help="CA bundle for LAN endpoints (optional)")
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("at least one --url is required")
    if args.samples < 1:
        parser.error("--samples must be >= 1")
    report = run(args.url, samples=args.samples, interval=args.interval,
                 timeout=args.timeout, ca_file=args.ca_file)
    print(json.dumps(report, ensure_ascii=False) if args.json else format_human(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
