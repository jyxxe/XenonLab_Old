"""Contract tests for the repeatable public/LAN sampling tool (#126)."""

from __future__ import annotations

import json

import pytest

from scripts import public_probe


def samples(*values):
    return [{"status": status, "ms": float(ms), "ok": status is not None and 200 <= status < 400}
            for status, ms in values]


def test_probe_reports_http_error_as_sample(monkeypatch):
    class Response:
        status = 200

        def read(self, _limit):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(public_probe.urllib.request, "urlopen", lambda *a, **k: Response())
    assert public_probe.probe("https://example.invalid/")["ok"] is True


def test_classify_counts_percentiles_and_failures():
    result = public_probe.classify(samples((200, 10), (200, 20), (530, 30), (None, 40)))
    assert result["samples"] == 4
    assert result["successes"] == 2
    assert result["status_counts"] == {"200": 2, "530": 1, "error": 1}
    assert result["median_ms"] == 20.0
    assert result["p95_ms"] == 40.0
    assert result["ok"] is False
    assert len(result["failures"]) == 2


def test_classify_all_success_and_empty():
    assert public_probe.classify(samples((200, 5), (302, 6)))["ok"] is True
    empty = public_probe.classify([])
    assert empty["ok"] is False and empty["median_ms"] == 0.0


def test_run_interleaves_urls_and_preserves_order():
    calls: list[str] = []

    def fake(url, *, timeout, ca_file):
        calls.append(url)
        return {"status": 200, "ms": 1.0, "ok": True}

    report = public_probe.run(["a", "b"], samples=2, interval=0, timeout=1,
                              ca_file="", sleep=lambda _s: None, probe_fn=fake)
    assert calls == ["a", "b", "a", "b"]
    assert [entry["url"] for entry in report["entries"]] == ["a", "b"]
    assert report["ok"] is True


def test_run_marks_failure_and_uses_injected_sleep():
    sleeps: list[float] = []

    def fake(url, *, timeout, ca_file):
        return {"status": 530, "ms": 2.0, "ok": False}

    report = public_probe.run(["x"], samples=2, interval=3, timeout=1, ca_file="",
                              sleep=sleeps.append, probe_fn=fake)
    assert sleeps == [3]
    assert report["ok"] is False
    assert report["entries"][0]["status_counts"] == {"530": 2}


def test_format_human_marks_failures_and_errors():
    report = {"entries": [{"url": "u", "ok": False, "successes": 1, "samples": 2,
                           "status_counts": {"200": 1, "error": 1}, "median_ms": 10,
                           "p95_ms": 20, "max_ms": 20,
                           "failures": [{"status": None, "ms": 1, "ok": False, "error": "timeout"}]}]}
    text = public_probe.format_human(report)
    assert "FAIL" in text and "failure: timeout" in text and "u -> FAIL" in text


def test_main_exit_codes_and_json(monkeypatch, capsys):
    monkeypatch.setattr(public_probe, "probe", lambda url, **kwargs: {"status": 200, "ms": 1.0, "ok": True})
    assert public_probe.main(["--url", "u", "--samples", "1", "--interval", "0"]) == 0
    assert public_probe.main(["--url", "u", "--samples", "1", "--interval", "0", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is True

    monkeypatch.setattr(public_probe, "probe", lambda url, **kwargs: {"status": 503, "ms": 1.0, "ok": False})
    assert public_probe.main(["--url", "u", "--samples", "1", "--interval", "0"]) == 1


def test_main_requires_url():
    with pytest.raises(SystemExit):
        public_probe.main(["--samples", "1"])
