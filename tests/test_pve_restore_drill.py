"""Isolated PVE restore-drill contract (issue #113).

These tests pin the safety refusals, the dry-run/execute split, the offline
boot isolation and the destroy-after-verify cleanup using an in-memory fake of
the PVE API; no network or production credential is touched.
"""

from __future__ import annotations

import json
import stat

import pytest

from scripts import pve_restore_drill as drill

ARCHIVE = "xeonolab-backup:backup/vzdump-qemu-106-2026_11_04-08_17_19.vma.zst"
SOURCE_CONFIG = {
    "name": "docker-server",
    "cores": 4,
    "memory": 16384,
    "onboot": 1,
    "net0": "virtio=BC:24:11:E2:EF:D3,bridge=vmbr0,firewall=1",
    "scsi0": "local-lvm:vm-106-disk-0,discard=on,iothread=1,size=80G,ssd=1",
}


class FakeClient:
    """Minimal in-memory PVE API used to exercise :func:`drill.run_drill`."""

    def __init__(self, *, agent_answers: bool = True, task_failure: str | None = None,
                 source_config: dict | None = None, shutdown_fails: bool = False):
        self.used = {100, 101, 106}
        self.archive = ARCHIVE
        self.archives: list[str] | None = None
        self.storage = "local-lvm"
        self.agent_answers = agent_answers
        self.task_failure = task_failure
        self.source_config = SOURCE_CONFIG if source_config is None else source_config
        self.shutdown_fails = shutdown_fails
        self.calls: list[tuple[str, str, dict | None]] = []
        self.config: dict = {"name": "docker-server", "cores": 4, "memory": 16384, "onboot": 1,
                             "net0": "virtio=BC:24:11:27:B1:E6,bridge=vmbr0,firewall=1",
                             "scsi0": "local-lvm:vm-190-disk-0,discard=on,iothread=1,size=80G,ssd=1"}
        self.restored = False
        self.started = False
        self.destroyed = False
        self.task_rounds: dict[str, int] = {}

    def _task(self, upid: str) -> str:
        self.task_rounds[upid] = self.task_rounds.get(upid, 0) + 1
        return upid

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None):
        return self.request("POST", path, {} if data is None else data)

    def put(self, path, data):
        return self.request("PUT", path, data)

    def delete(self, path):
        return self.request("DELETE", path)

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method == "GET" and path == "/nodes/pve/qemu":
            return [{"vmid": vmid} for vmid in sorted(self.used)]
        if method == "GET" and path == "/nodes/pve/storage":
            return [
                {"storage": self.storage, "content": "images,rootdir", "active": 1},
                {"storage": "xeonolab-backup", "content": "backup", "active": 1},
            ]
        if method == "GET" and path == "/nodes/pve/storage/xeonolab-backup/content?content=backup":
            return [{"volid": volid} for volid in (self.archives or [self.archive])]
        if method == "GET" and path == f"/nodes/pve/storage/{self.storage}/content":
            return [{"volid": f"{self.storage}:vm-190-disk-0"}] if self.restored and not self.destroyed else []
        if path.startswith("/nodes/pve/tasks/"):
            upid = path[len("/nodes/pve/tasks/"):].split("/")[0]
            rounds = self.task_rounds.get(upid, 0) + 1
            self.task_rounds[upid] = rounds
            if rounds <= 1:
                return {"status": "running"}
            if self.shutdown_fails and upid.startswith("UPID:shutdown-failed"):
                return {"status": "stopped", "exitstatus": "VM quit/powerdown failed"}
            if self.task_failure:
                return {"status": "stopped", "exitstatus": self.task_failure}
            return {"status": "stopped", "exitstatus": "OK"}
        if method == "POST" and path == "/nodes/pve/qemu":
            self.restored = True
            self.used.add(190)
            return self._task("UPID:restore")
        if method == "GET" and path == "/nodes/pve/qemu/106/config":
            return dict(self.source_config)
        if method == "GET" and path == "/nodes/pve/qemu/190/config":
            return dict(self.config)
        if method == "PUT" and path == "/nodes/pve/qemu/190/config":
            self.config.update(data or {})
            return None
        if method == "POST" and path == "/nodes/pve/qemu/106/agent":
            return {"result": {"pretty-name": "Ubuntu 26.04.1 LTS"}}
        if method == "POST" and path == "/nodes/pve/qemu/190/agent":
            command = (data or {}).get("command")
            if not self.agent_answers:
                raise drill.DrillError("agent unavailable")
            if command == "ping":
                return {"result": {}}
            if command == "get-osinfo":
                return {"result": {"pretty-name": "Ubuntu 26.04.1 LTS", "kernel-release": "7.0.0-38"}}
            if command == "get-host-name":
                return {"result": {"host-name": "docker-server"}}
            if command == "get-fsinfo":
                return {"result": [{"mountpoint": "/", "total-bytes": 82068492288,
                                    "used-bytes": 4538904576, "type": "ext4"}]}
            raise drill.DrillError(f"unsupported command {command}")
        if method == "POST" and path == "/nodes/pve/qemu/190/status/start":
            self.started = True
            return self._task("UPID:start")
        if method == "POST" and path == "/nodes/pve/qemu/190/status/shutdown":
            if self.shutdown_fails:
                raise drill.DrillError("VM quit/powerdown failed")
            self.started = False
            return self._task("UPID:shutdown")
        if method == "POST" and path == "/nodes/pve/qemu/190/status/stop":
            self.started = False
            return self._task("UPID:stop")
        if method == "GET" and path == "/nodes/pve/qemu/190/status/current":
            return {"status": "running" if self.started else "stopped"}
        if method == "DELETE" and path == "/nodes/pve/qemu/190?purge=1&destroy-unreferenced-disks=1":
            self.destroyed = True
            self.used.discard(190)
            return self._task("UPID:destroy")
        raise AssertionError(f"unexpected call {method} {path}")


def options(**overrides):
    values = dict(node="pve", endpoint="https://pve.invalid:8006", token_file="", ca_file="",
                  insecure=True, archive=ARCHIVE, vmid=190, storage="local-lvm", source_vmid=106,
                  no_boot=False, boot=True, keep=False, execute=True, timeout=5.0, agent_timeout=5.0,
                  shutdown_timeout=60.0, latest_archive_for=None, archive_storage="xeonolab-backup")
    values.update(overrides)
    return type("Options", (), values)()


def no_sleep(_seconds):
    return None


def test_check_target_rejects_production_reserved_and_in_use():
    with pytest.raises(drill.SafetyRefused):
        drill.check_target(106, set())
    with pytest.raises(drill.SafetyRefused):
        drill.check_target(990, set())
    with pytest.raises(drill.SafetyRefused):
        drill.check_target(190, {190})
    with pytest.raises(drill.SafetyRefused):
        drill.check_target(50, set())
    drill.check_target(190, {100, 101, 106})


def test_force_link_down_replaces_existing_value_and_keeps_bridge():
    isolated = drill.force_link_down("virtio=BC:24:11:E2:EF:D3,bridge=vmbr0,firewall=1,link_down=0")
    assert isolated.startswith("virtio=BC:24:11:E2:EF:D3")
    assert "bridge=vmbr0" in isolated
    assert isolated.count("link_down=") == 1
    assert isolated.endswith("link_down=1")
    with pytest.raises(drill.SafetyRefused):
        drill.force_link_down("")


def test_validate_archive_accepts_vma_and_rejects_paths():
    assert drill.validate_archive(ARCHIVE) == ARCHIVE
    for bad in ("", "192.168.0.10:/tmp/x.vma.zst", "local:iso/x.iso", "local:../x.vma.zst",
                "local:backup/x.tar.gz"):
        with pytest.raises(drill.SafetyRefused):
            drill.validate_archive(bad)


def test_disk_sizes_extraction_ignores_non_disks():
    sizes = drill.disk_sizes(SOURCE_CONFIG)
    assert sizes == {"scsi0": "80G"}


def test_read_token_requires_0600_and_single_line(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    token = tmp_path / "token"
    token.write_text("root@pam!a4=27f5ba10-5f0c-4265-812a-c004aff86ead\n")
    token.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert drill.read_token(str(token)).startswith("root@pam!a4=")
    token.chmod(0o644)
    with pytest.raises(drill.SafetyRefused):
        drill.read_token(str(token))


def test_wait_task_times_out_and_propagates_failure():
    class Running:
        def get(self, path):
            return {"status": "running"}

    with pytest.raises(drill.DrillError):
        drill.wait_task(Running(), "pve", "UPID:pve:x", timeout=0, sleep=no_sleep)
    failed = FakeClient(task_failure="restore failed: archive corrupt")
    with pytest.raises(drill.DrillError):
        drill.wait_task(failed, "pve", "UPID:restore", timeout=5, sleep=no_sleep)


def test_archive_and_restore_storages_are_resolved_separately():
    client = FakeClient()
    report = drill.run_drill(options(execute=False), client=client, sleep=no_sleep)
    assert report["preflight"]["archive_present"] is True
    assert not any("local-lvm/content?content=backup" in path for _method, path, _data in client.calls)
    missing = FakeClient()
    missing.archive = "xeonolab-backup:backup/absent.vma.zst"
    with pytest.raises(drill.SafetyRefused):
        drill.run_drill(options(execute=False), client=missing, sleep=no_sleep)


def test_dry_run_never_mutates():
    client = FakeClient()
    report = drill.run_drill(options(execute=False), client=client, sleep=no_sleep)
    assert [step["step"] for step in report["plan"]] == ["restore", "isolate", "start", "verify-agent", "cleanup"]
    mutations = [
        call for call in client.calls
        if call[0] != "GET" and not call[1].endswith("/agent")
    ]
    assert mutations == []
    assert client.restored is False


def test_execute_restores_isolated_boot_verifies_and_cleans_up():
    client = FakeClient()
    report = drill.run_drill(options(), client=client, sleep=no_sleep)
    steps = [step["step"] for step in report["steps"]]
    assert steps == ["restore", "boot-verify"]
    put = [data for method, path, data in client.calls if method == "PUT" and path == "/nodes/pve/qemu/190/config"]
    assert put and put[0]["onboot"] == 0 and "link_down=1" in put[0]["net0"]
    assert report["verification"]["checks"]["disk_sizes_match_source"] is True
    assert report["verification"]["checks"]["cores_match_source"] is True
    assert report["verification"]["checks"]["hostname_present"] is True
    assert report["verification"]["checks"]["root_total_bytes"] == 82068492288
    assert report["cleanup"]["vmid_absent"] is True
    assert report["cleanup"]["volumes_remaining"] == []
    assert report["ok"] is True
    assert client.destroyed is True


def test_failed_agent_reports_error_and_cleans_up():
    client = FakeClient(agent_answers=False)
    report = drill.run_drill(options(agent_timeout=0), client=client, sleep=no_sleep)
    assert "guest agent did not answer" in report["error"]
    assert report.get("verification") is None
    assert report["cleanup"]["vmid_absent"] is True
    assert report["ok"] is False


def test_keep_flag_skips_cleanup():
    client = FakeClient(agent_answers=False)
    report = drill.run_drill(options(agent_timeout=0, keep=True), client=client, sleep=no_sleep)
    assert "cleanup" not in report
    assert "error" in report
    assert client.destroyed is False
    assert not any(method == "DELETE" for method, _path, _data in client.calls)


def test_source_data_disk_backup_0_is_reported_not_failed():
    source = dict(SOURCE_CONFIG)
    source["scsi1"] = "local-lvm:vm-106-disk-1,discard=on,size=200G"
    client = FakeClient(source_config=source)
    report = drill.run_drill(options(), client=client, sleep=no_sleep)
    checks = report["verification"]["checks"]
    assert checks["disk_sizes_match_source"] is True
    assert checks["source_disks_not_restored"] == ["scsi1"]
    assert report["ok"] is True


def test_failed_acpi_shutdown_falls_back_to_hard_stop():
    client = FakeClient(shutdown_fails=True)
    report = drill.run_drill(options(shutdown_timeout=0), client=client, sleep=no_sleep)
    assert "VM quit/powerdown failed" in report["cleanup"]["shutdown_error"]
    assert report["cleanup"]["hard_stopped"] is True
    assert report["cleanup"]["vmid_absent"] is True
    assert report["ok"] is True
    assert client.destroyed is True


def test_select_latest_archive_picks_newest_for_vmid():
    volids = [
        "xeonolab-backup:backup/vzdump-qemu-106-2026_11_04-08_17_19.vma.zst",
        "xeonolab-backup:backup/vzdump-qemu-106-2026_11_07-19_00_00.vma.zst",
        "xeonolab-backup:backup/vzdump-qemu-109-2026_11_03-22_16_56.vma.zst",
        "xeonolab-backup:backup/not-a-vzdump-file.vma.zst",
    ]
    assert drill.select_latest_archive(volids, 106) == (
        "xeonolab-backup:backup/vzdump-qemu-106-2026_11_07-19_00_00.vma.zst"
    )
    assert drill.select_latest_archive(volids, 109).endswith("vzdump-qemu-109-2026_11_03-22_16_56.vma.zst")
    with pytest.raises(drill.SafetyRefused):
        drill.select_latest_archive(volids, 105)
    with pytest.raises(drill.SafetyRefused):
        drill.select_latest_archive([], 106)


def test_dry_run_with_latest_archive_selection():
    client = FakeClient()
    client.archives = [
        "xeonolab-backup:backup/vzdump-qemu-106-2026_11_04-08_17_19.vma.zst",
        "xeonolab-backup:backup/vzdump-qemu-106-2026_11_07-19_00_00.vma.zst",
    ]
    report = drill.run_drill(options(execute=False, archive="", latest_archive_for=106), client=client,
                             sleep=no_sleep)
    assert report["archive"].endswith("vzdump-qemu-106-2026_11_07-19_00_00.vma.zst")
    assert report["archive_origin"] == "latest-for-vmid-106"
    assert report["plan"] and client.restored is False


def test_parse_args_requires_exactly_one_archive_source():
    with pytest.raises(SystemExit):
        drill.parse_args(["--vmid", "190"])
    with pytest.raises(SystemExit):
        drill.parse_args(["--vmid", "190", "--archive", ARCHIVE, "--latest-archive-for", "106"])
    parsed = drill.parse_args(["--vmid", "190", "--latest-archive-for", "106"])
    assert parsed.archive == "" and parsed.latest_archive_for == 106
    compact = drill.parse_args(["--vmid", "190", "--latest-archive-for", "106", "--compact"])
    assert compact.compact is True and drill.parse_args(["--vmid", "190", "--archive", ARCHIVE]).compact is False


def test_main_prints_refusal_and_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drill, "read_token", lambda _path: "root@pam!a4=secret")
    monkeypatch.setattr(drill, "PveClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(drill, "run_drill", lambda options, client: (_ for _ in ()).throw(
        drill.SafetyRefused("scratch vmid 190 is already in use")))
    assert drill.main(["--archive", ARCHIVE, "--vmid", "190", "--token-file", "x"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "already in use" in payload["refused"]
