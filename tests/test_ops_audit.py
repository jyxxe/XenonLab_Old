"""Read-only fleet audit contract (issue #391).

The maintenance loop needs one command that surfaces disk pressure, failed
units, release-retention drift, timers, backup age and certificate expiry
without writing to any target. These tests pin the parsing and threshold
rules; the SSH/network paths are exercised by the operator, not CI.
"""

from scripts import ops_audit

SAMPLE = """###hostname
web-server
###df
/dev/sda1 39748564 7340032 30441232 20% /
###failed
● openipmi.service           loaded failed failed LSB: OpenIPMI Driver init script
###timers
Tue 2026-11-03 04:43:21 UTC 12h - xeonolab-release-prune.timer xeonolab-release-prune.service
###release
/opt/xeonolab/releases/ccd3c4ff4647f0dd4091e4ee6e8d4af9d0a1d4e8
4
###backup
/var/backups/xeonolab/embedding-config-20261002
1790000000
###errors
   6226 -: failed to get terminal attributes: Input/output error
      1 systemd Failed to start openipmi.service
"""


def test_parse_host_output_extracts_report_fields():
    report = ops_audit.parse_host_output(SAMPLE)
    assert report["hostname"] == "web-server"
    assert report["disk"]["used_percent"] == 20
    assert report["disk"]["available_kib"] == 30441232
    assert report["failed_units"] == ["openipmi.service"]
    assert report["release_count"] == 4
    assert report["backup_path"].endswith("embedding-config-20261002")
    assert report["backup_age_hours"] > 1000
    assert report["error_lines"][0].startswith("6226 ")


def test_evaluate_allows_known_hardware_service_and_ignores_manual_archives():
    report = ops_audit.parse_host_output(SAMPLE)
    findings = ops_audit.evaluate(
        {"192.168.0.101": report}, {},
        min_free_mib=1024, max_used_percent=90, keep_releases=4,
        max_backup_age_hours=48, allowed_failed=("openipmi.service",),
    )
    # SAMPLE has only a one-off manual archive directory, not a scheduled domain;
    # it is reported in JSON but must not produce a freshness finding.
    assert not any("backup" in item for item in findings)
    assert not any("failed units" in item for item in findings)
    assert not any("release directories" in item for item in findings)


def test_evaluate_flags_unexpected_failures_disk_and_certificates():
    report = ops_audit.parse_host_output(SAMPLE)
    report["disk"] = {"filesystem": "/dev/sda1", "total_kib": 100, "used_kib": 96,
                      "available_kib": 4096, "used_percent": 96, "mount": "/"}
    report["failed_units"] = ["postgresql@18-main.service"]
    report["release_count"] = 9
    findings = ops_audit.evaluate(
        {"192.168.0.110": report}, {"192.168.0.101:9443": 2},
        min_free_mib=5120, max_used_percent=90, keep_releases=4,
        max_backup_age_hours=48, allowed_failed=("openipmi.service",), min_cert_days=7,
    )
    joined = "\n".join(findings)
    assert "96% used" in joined
    assert "failed units: postgresql@18-main.service" in joined
    assert "9 release directories" in joined
    assert "certificate expires in 2 day(s)" in joined


def test_parse_df_line_rejects_garbage():
    assert ops_audit.parse_df_line("not a df line") is None
    parsed = ops_audit.parse_df_line("/dev/sda1 100 50 50 50% /")
    assert parsed == {"filesystem": "/dev/sda1", "total_kib": 100, "used_kib": 50,
                      "available_kib": 50, "used_percent": 50, "mount": "/"}


BACKUP_SAMPLE = """###hostname
xelibrary
###df
/dev/sda1 39748564 7340032 30441232 20% /
###failed
###timers
###release
/opt/xeonolab/releases/ccd3c4ff4647f0dd4091e4ee6e8d4af9d0a1d4e8
6
###backup
/var/backups/xeonolab/xelibrary
1700000000
###backupdomains
xelibrary xelibrary-20261103T033000Z 1700000000
forgejo none 0
###backupunits
xeonolab-xelibrary-backup.timer active enabled
xeonolab-xelibrary-backup.service inactive enabled failed
###errors
"""


def test_parse_host_output_extracts_backup_domains_and_units():
    report = ops_audit.parse_host_output(BACKUP_SAMPLE)
    assert report["backup_domains"]["xelibrary"]["snapshot"] == "xelibrary-20261103T033000Z"
    assert report["backup_domains"]["xelibrary"]["age_hours"] > 1000
    assert report["backup_domains"]["forgejo"] == {"snapshot": None}
    units = {unit["name"]: unit for unit in report["backup_units"]}
    assert units["xeonolab-xelibrary-backup.timer"]["active"] == "active"
    assert units["xeonolab-xelibrary-backup.service"]["result"] == "failed"


def test_evaluate_flags_stale_and_missing_backup_domains_and_units():
    report = ops_audit.parse_host_output(BACKUP_SAMPLE)
    findings = ops_audit.evaluate(
        {"192.168.0.104": report}, {},
        min_free_mib=1024, max_used_percent=90, keep_releases=4,
        max_backup_age_hours=36, allowed_failed=(),
    )
    joined = "\n".join(findings)
    assert "xelibrary backup is" in joined
    assert "forgejo backup directory has no snapshot" in joined
    assert "backup unit xeonolab-xelibrary-backup.service last run failed" in joined
    # Legacy global backup checks must not duplicate per-domain findings.
    assert "no /var/backups/xeonolab snapshot found" not in joined
    # Six releases is a tolerated transient window between deploy and prune.
    assert "release directories" not in joined


def test_evaluate_tolerates_deploy_window_but_flags_real_release_drift():
    report = ops_audit.parse_host_output(BACKUP_SAMPLE)
    report["backup_domains"] = {}
    report["backup_units"] = []
    report["backup_age_hours"] = 1.0
    assert not any("release directories" in item for item in ops_audit.evaluate(
        {"192.168.0.104": report}, {}, keep_releases=4, release_tolerance=2,
        max_backup_age_hours=36, allowed_failed=()))
    report["release_count"] = 7
    assert any("7 release directories" in item for item in ops_audit.evaluate(
        {"192.168.0.104": report}, {}, keep_releases=4, release_tolerance=2,
        max_backup_age_hours=36, allowed_failed=()))


def test_evaluate_pve_backups_flags_empty_disabled_missing_and_stale():
    now = 1_800_000_000
    assert ops_audit.evaluate_pve_backups({}, now=now) == ["pve: no scheduled VM backup jobs"]
    report = ops_audit.evaluate_pve_backups(
        {"error": "PVE API unavailable: timeout"}, now=now
    )
    assert report and report[0].startswith("pve: PVE API unavailable")

    jobs = [
        {"id": "job-enabled", "vmid": "101,109", "storage": "xeonolab-backup", "enabled": 1},
        {"id": "job-disabled", "vmid": "105", "storage": "xeonolab-backup", "enabled": 0},
    ]
    archives = {"xeonolab-backup:101": now - 3600 * 24, "xeonolab-backup:105": now - 3600}
    findings = ops_audit.evaluate_pve_backups(
        {"jobs": jobs, "archives": archives}, now=now, max_age_hours=48
    )
    joined = "\n".join(findings)
    assert "pve: backup job-disabled is disabled" in joined
    assert "pve: VM109 has no vzdump archive" in joined
    assert "VM101" not in joined
    assert "VM105" not in joined

    stale = ops_audit.evaluate_pve_backups(
        {"jobs": [jobs[0]], "archives": {"xeonolab-backup:101": now - 3600 * 240,
                                         "xeonolab-backup:109": now - 3600}},
        now=now, max_age_hours=48,
    )
    assert any("VM101 newest archive is" in line for line in stale)
    assert not any("VM109 newest archive is" in line for line in stale)

MONITORING_SAMPLE = """###hostname
dev-agent
###df
/dev/sda1 39748564 7340032 30441232 20% /
###failed
###timers
###release
/opt/xeonolab/releases/eb68a8a85f6dbfd6b32b918f212bab98ae93cff4
4
###backup
none
###monitoring
xeonolab-public-probe.timer active enabled
xeonolab-pve-restore-drill.timer active enabled
/var/lib/devagent/logs/public-probe.jsonl|1793930000|12
/var/lib/devagent/logs/pve-restore-drill.jsonl|1793928600|1
###errors
"""


def test_parse_host_output_extracts_monitoring_state():
    report = ops_audit.parse_host_output(MONITORING_SAMPLE)
    monitoring = report["monitoring"]
    assert {unit["name"] for unit in monitoring["units"]} == {
        "xeonolab-public-probe.timer", "xeonolab-pve-restore-drill.timer",
    }
    assert monitoring["logs"]["/var/lib/devagent/logs/public-probe.jsonl"] == {"mtime": 1793930000, "lines": 12}


def test_evaluate_monitoring_healthy_and_stale():
    report = ops_audit.parse_host_output(MONITORING_SAMPLE)
    now = 1793930300  # 5 minutes after the probe log write
    healthy = ops_audit.evaluate_monitoring(report["monitoring"], "dev-agent", now=now)
    assert healthy == []
    stale = ops_audit.evaluate_monitoring(report["monitoring"], "dev-agent", now=now + 3 * 3600)
    assert any("public probe log is" in item for item in stale)


def test_evaluate_monitoring_flags_missing_timers_and_logs():
    findings = ops_audit.evaluate_monitoring({}, "dev-agent", now=1793930300)
    joined = "\n".join(findings)
    assert "public-probe.timer is missing" in joined
    assert "pve-restore-drill.timer is missing" in joined
    assert "public probe log is missing" in joined
    assert "restore drill log is missing" in joined

    inactive = {"units": [
        {"name": "xeonolab-public-probe.timer", "active": "inactive", "enabled": "enabled"},
        {"name": "xeonolab-pve-restore-drill.timer", "active": "active", "enabled": "disabled"},
    ], "logs": {}}
    joined = "\n".join(ops_audit.evaluate_monitoring(inactive, "dev-agent", now=1793930300))
    assert "xeonolab-public-probe.timer is inactive" in joined
    assert "xeonolab-pve-restore-drill.timer is disabled" in joined


def test_evaluate_only_checks_monitoring_on_dev_agent_host():
    report = ops_audit.parse_host_output(SAMPLE)  # hostname web-server, no monitoring section
    findings = ops_audit.evaluate({"192.168.0.101": report}, {}, allowed_failed=("openipmi.service",))
    assert not any("monitoring" in item or "probe log" in item for item in findings)


def test_remote_commands_ignore_in_progress_temp_directories():
    # A running data backup leaves a `.data-dump-*` temp directory in the domain
    # directory; it must never be selected as the "latest snapshot".
    from scripts import backup_verify
    assert "-name '[!.]*'" in backup_verify.REMOTE_COMMAND
    assert "-name '[!.]*'" in ops_audit.REMOTE_COMMAND


CLOUDFLARED_SAMPLE = """###hostname
web-server
###df
/dev/sda1 39748564 7340032 30441232 20% /
###failed
###timers
###release
/opt/xeonolab/releases/8d8a3667f994d69ed6c7abfd288af90fedee26d2
4
###backup
none
###monitoring
###cloudflared
version 2026.10.7
active active
restarts 0
drops 0
ha 4
###errors
"""


def test_parse_host_output_extracts_cloudflared_state():
    report = ops_audit.parse_host_output(CLOUDFLARED_SAMPLE)
    assert report["cloudflared"] == {
        "present": True, "version": "2026.10.7", "active": "active",
        "restarts": 0, "drops_since_start": 0, "ha_connections": 4,
    }
    absent = ops_audit.parse_host_output("###hostname\nh\n###cloudflared\nabsent\n")
    assert absent["cloudflared"]["present"] is False


def test_evaluate_cloudflared_healthy_and_findings():
    healthy = {"present": True, "active": "active", "ha_connections": 4, "drops_since_start": 0}
    assert ops_audit.evaluate_cloudflared(healthy, "web-server") == []
    assert ops_audit.evaluate_cloudflared({"present": False}, "web-server") == []
    down = {"present": True, "active": "failed", "ha_connections": 0, "drops_since_start": 30}
    joined = "\n".join(ops_audit.evaluate_cloudflared(down, "web-server"))
    assert "cloudflared is failed" in joined
    assert "no active HA connections" in joined
    assert "lost the edge 30 times since start" in joined
    # Explicitly disabling the threshold keeps only the other checks.
    only_down = ops_audit.evaluate_cloudflared(down, "web-server", max_drops_24h=-1)
    assert not any("lost the edge" in item for item in only_down)
