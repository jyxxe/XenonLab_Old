"""Contract tests for the read-only nightly backup re-verification tool (#390)."""

from __future__ import annotations

import json

from scripts import backup_verify

SAMPLE = """###hostname
web-server
###backupverify
desktop|desktop-20261105T042936Z|ok: verified /var/backups/xeonolab/desktop/desktop-20261105T042936Z: 81920 accounts bytes, 1 wallpapers, 3 files
data|data-20261105T045820Z|ok: verified /var/backups/xeonolab/data/data-20261105T045820Z: 21466787 bytes, 2116 entries
"""

FAILED = """###hostname
dev-agent
###backupverify
data|data-20261105T050137Z|fail(2): backup error: manifest mismatch
"""


def test_parse_report_splits_domains_and_status():
    report = backup_verify.parse_report(SAMPLE)
    assert report["hostname"] == "web-server"
    assert [entry["domain"] for entry in report["entries"]] == ["desktop", "data"]
    assert all(entry["ok"] for entry in report["entries"])
    assert report["entries"][0]["snapshot"] == "desktop-20261105T042936Z"
    failed = backup_verify.parse_report(FAILED)["entries"][0]
    assert failed["ok"] is False and failed["detail"].startswith("fail(2):")


def test_parse_report_ignores_unknown_noise():
    text = "###hostname\nhost1\nsome noise\n###backupverify\nnot-a-domain-line\n"
    report = backup_verify.parse_report(text)
    assert report["hostname"] == "host1"
    assert report["entries"] == []


def test_summarize_reports_failures_and_ssh_errors():
    reports = [
        {"host": "192.168.0.101", "hostname": "web-server",
         "entries": [{"domain": "desktop", "snapshot": "d1", "ok": True, "detail": "ok: verified"}]},
        {"host": "192.168.0.111", "hostname": "dev-agent",
         "entries": [{"domain": "data", "snapshot": "d2", "ok": False, "detail": "fail(2): x"}]},
        {"host": "192.168.0.100", "hostname": "unknown", "error": "timeout", "entries": []},
    ]
    summary = backup_verify.summarize(reports)
    assert summary["domains"] == 2
    assert summary["ok"] is False
    assert [failure["domain"] for failure in summary["failures"]] == ["data"]
    assert summary["ssh_errors"] == ["192.168.0.100"]


def test_summarize_ok_requires_at_least_one_entry():
    assert backup_verify.summarize([])["ok"] is False


def test_format_human_marks_failures():
    reports = [{"host": "h", "hostname": "n",
                "entries": [{"domain": "data", "snapshot": "d", "ok": False, "detail": "fail(2): boom"}]}]
    text = backup_verify.format_human(backup_verify.summarize(reports), reports)
    assert "FAIL" in text and "data d" in text and "failures: 1" in text


def test_main_exit_codes_and_json(monkeypatch, capsys):
    def fake(host, user, key, known_hosts, timeout):
        if host.endswith("101"):
            return {"host": host, "hostname": "web-server",
                    "entries": [{"domain": "desktop", "snapshot": "s", "ok": True, "detail": "ok: verified"}]}
        return {"host": host, "hostname": "other", "entries": []}

    monkeypatch.setattr(backup_verify, "verify_host", fake)
    assert backup_verify.main(["--hosts", "192.168.0.101,192.168.0.110"]) == 0
    assert backup_verify.main(["--hosts", "192.168.0.110"]) == 2

    monkeypatch.setattr(backup_verify, "verify_host", lambda *args, **kwargs: {
        "host": "h", "hostname": "n",
        "entries": [{"domain": "data", "snapshot": "s", "ok": False, "detail": "fail(2): x"}]})
    assert backup_verify.main(["--hosts", "h"]) == 1
    assert backup_verify.main(["--hosts", "h", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["summary"]["ok"] is False


def test_domains_constant_covers_expected_backups():
    assert set(backup_verify.DOMAINS) == {"xelibrary", "forgejo", "keycloak", "desktop", "data"}
