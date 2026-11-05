"""Contract tests for the public probe JSONL report aggregator (#126)."""

from __future__ import annotations

import datetime
import json

from scripts import public_probe_report

ROUNDS = [
    {"started_at": "2026-11-05T20:45:18Z", "entries": [
        {"url": "https://public/", "samples": 6, "successes": 6, "failures": [],
         "status_counts": {"200": 6}, "median_ms": 837.0, "p95_ms": 856.0, "ok": True},
        {"url": "https://lan/", "samples": 6, "successes": 6, "failures": [],
         "status_counts": {"200": 6}, "median_ms": 4.0, "p95_ms": 5.0, "ok": True},
    ]},
    {"started_at": "2026-11-05T20:50:46Z", "entries": [
        {"url": "https://public/", "samples": 6, "successes": 4, "failures": [
            {"status": None, "error": "handshake timed out"}, {"status": 530, "ms": 1.0}],
         "status_counts": {"200": 4, "error": 1, "530": 1}, "median_ms": 2100.0, "p95_ms": 15499.0, "ok": False},
        {"url": "https://lan/", "samples": 6, "successes": 6, "failures": [],
         "status_counts": {"200": 6}, "median_ms": 4.0, "p95_ms": 5.0, "ok": True},
    ]},
]


def write_log(tmp_path, rounds=ROUNDS, extra_lines=()):
    path = tmp_path / "probe.jsonl"
    lines = [json.dumps(round_data) for round_data in rounds]
    lines.extend(extra_lines)
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_load_rounds_skips_invalid_lines(tmp_path):
    path = write_log(tmp_path, extra_lines=["not-json", '{"entries": "nope"}'])
    rounds, invalid = public_probe_report.load_rounds(path)
    assert len(rounds) == 2
    assert invalid == 2


def test_load_rounds_since_filter(tmp_path):
    path = write_log(tmp_path)
    now = datetime.datetime(2026, 11, 5, 21, 0, tzinfo=datetime.timezone.utc)
    rounds, _ = public_probe_report.load_rounds(path, since_hours=0.2, now=now)
    assert len(rounds) == 1
    assert rounds[0]["started_at"] == "2026-11-05T20:50:46Z"


def test_summarise_aggregates_per_url():
    report = public_probe_report.summarise(ROUNDS)
    assert report["rounds"] == 2
    assert report["window"]["from"] == "2026-11-05T20:45:18Z"
    by_url = {entry["url"]: entry for entry in report["entries"]}
    public = by_url["https://public/"]
    assert public["samples"] == 12 and public["successes"] == 10
    assert public["status_counts"] == {"200": 10, "error": 1, "530": 1}
    assert public["success_rate"] == 0.8333
    assert public["median_of_round_medians_ms"] == 1468.5
    assert public["worst_p95_ms"] == 15499.0
    assert len(public["failure_rounds"]) == 1
    assert public["ok"] is False
    assert by_url["https://lan/"]["ok"] is True
    assert report["ok"] is False


def test_summarise_empty():
    report = public_probe_report.summarise([])
    assert report["ok"] is False and report["rounds"] == 0
    assert report["window"] == {"from": None, "to": None}


def test_format_human_mentions_failure_window():
    report = public_probe_report.summarise(ROUNDS)
    text = public_probe_report.format_human(report)
    assert "rounds: 2" in text
    assert "failed rounds: 1" in text
    assert "2026-11-05T20:50:46Z" in text
    assert "handshake timed out" in text


def test_main_exit_codes(tmp_path, capsys):
    path = write_log(tmp_path)
    assert public_probe_report.main(["--log", path]) == 0
    assert public_probe_report.main(["--log", path, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["rounds"] == 2
    assert public_probe_report.main(["--log", path, "--fail-on-errors"]) == 1
    missing = tmp_path / "missing.jsonl"
    assert public_probe_report.main(["--log", str(missing)]) == 2
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n")
    assert public_probe_report.main(["--log", str(empty)]) == 1
