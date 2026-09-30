import stat
from pathlib import Path

import pytest

from infra.systemd import caddy_sync


def make_layout(tmp_path):
    caddy_dir = tmp_path / "etc" / "caddy"
    caddy_dir.mkdir(parents=True)
    caddyfile = caddy_dir / "Caddyfile"
    caddyfile.write_text(
        "import /etc/caddy/xeonolab.caddy\n"
        "import /etc/caddy/cloudflare-origin.caddy\n"
        "import /etc/caddy/forgejo-webhook.caddy\n"
    )
    target = caddy_dir / "cloudflare-origin.caddy"
    target.write_text("old cloudflare config\n")
    target.chmod(0o640)
    source = tmp_path / "release" / "infra" / "network" / "cloudflare-origin.caddy"
    source.parent.mkdir(parents=True)
    source.write_text("new cloudflare config\n")
    return caddyfile, target, source


def test_install_validates_full_local_caddyfile_then_preserves_target_metadata(tmp_path):
    caddyfile, target, source = make_layout(tmp_path)
    original_stat = target.stat()
    observed = []

    def validate_command(args, check):
        assert check is True
        candidate_config = Path(args[args.index("--config") + 1]).read_text()
        observed.append(candidate_config)

    changed = caddy_sync.install(
        source, target, caddyfile, runner=validate_command
    )

    assert changed is True
    assert target.read_text() == source.read_text()
    installed_stat = target.stat()
    assert stat.S_IMODE(installed_stat.st_mode) == stat.S_IMODE(original_stat.st_mode)
    assert (installed_stat.st_uid, installed_stat.st_gid) == (original_stat.st_uid, original_stat.st_gid)
    assert len(observed) == 1
    assert "import /etc/caddy/xeonolab.caddy" in observed[0]
    assert "import /etc/caddy/forgejo-webhook.caddy" in observed[0]
    assert "import " + str(source) in observed[0]


def test_validation_failure_leaves_live_include_untouched_and_is_idempotent(tmp_path):
    caddyfile, target, source = make_layout(tmp_path)
    original = target.read_bytes()

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("synthetic caddy validation failure")

    with pytest.raises(RuntimeError, match="validation failure"):
        caddy_sync.install(source, target, caddyfile, runner=fail_validation)
    assert target.read_bytes() == original

    def success(_args, check):
        assert check is True

    assert caddy_sync.install(source, target, caddyfile, runner=success) is True
    before = target.stat().st_mtime_ns
    assert caddy_sync.install(source, target, caddyfile, runner=success) is False
    assert target.stat().st_mtime_ns == before


def test_restore_validates_and_restores_previous_bytes_and_metadata(tmp_path):
    caddyfile, target, source = make_layout(tmp_path)
    snapshot = caddy_sync.capture(target).as_json()
    original_mode = stat.S_IMODE(target.stat().st_mode)

    def success(_args, check):
        assert check is True

    caddy_sync.install(source, target, caddyfile, runner=success)
    assert caddy_sync.restore(snapshot, target, caddyfile, runner=success) is True
    assert target.read_text() == "old cloudflare config\n"
    assert stat.S_IMODE(target.stat().st_mode) == original_mode
    assert caddy_sync.restore(snapshot, target, caddyfile, runner=success) is False


def test_validation_requires_exactly_one_fixed_import(tmp_path):
    caddyfile, target, source = make_layout(tmp_path)
    caddyfile.write_text("import /etc/caddy/xeonolab.caddy\n")

    with pytest.raises(ValueError, match="exactly one"):
        caddy_sync.install(source, target, caddyfile, runner=lambda *_args, **_kwargs: None)


@pytest.mark.parametrize(
    "invalid_import",
    [
        "import\n /etc/caddy/cloudflare-origin.caddy\n",
        "import /etc/caddy/cloudflare-origin.caddy extra\n",
        "import\v/etc/caddy/cloudflare-origin.caddy\n",
    ],
)
def test_import_match_rejects_multiline_or_malformed_directives(tmp_path, invalid_import):
    caddyfile, target, source = make_layout(tmp_path)
    caddyfile.write_text("import /etc/caddy/xeonolab.caddy\n" + invalid_import)

    with pytest.raises(ValueError, match="exactly one"):
        caddy_sync.install(source, target, caddyfile, runner=lambda *_args, **_kwargs: None)


def test_activation_state_is_fsynced_before_replace_and_directory_fsynced(tmp_path, monkeypatch):
    state_path = tmp_path / "activations" / "deployment.json"
    state_path.parent.mkdir()
    events = []
    real_fsync = caddy_sync.os.fsync
    real_replace = caddy_sync.os.replace

    def record_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def record_replace(source, target):
        events.append("replace")
        return real_replace(source, target)

    monkeypatch.setattr(caddy_sync.os, "fsync", record_fsync)
    monkeypatch.setattr(caddy_sync.os, "replace", record_replace)
    caddy_sync.atomic_write_json(state_path, {"snapshot": "old"})

    assert events == ["fsync", "replace", "fsync"]
    assert state_path.read_text() == '{"snapshot":"old"}'
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_state_directory_fsync_failure_prevents_activation_state_success(tmp_path, monkeypatch):
    state_path = tmp_path / "activations" / "deployment.json"
    state_path.parent.mkdir()
    real_fsync = caddy_sync.os.fsync
    count = 0

    def fail_directory_fsync(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic activation directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(caddy_sync.os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync failure"):
        caddy_sync.atomic_write_json(state_path, {"snapshot": "old"})
    assert state_path.exists()


def test_include_mutation_is_reported_before_directory_fsync_failure_and_restorable(
    tmp_path, monkeypatch
):
    caddyfile, target, source = make_layout(tmp_path)
    snapshot = caddy_sync.capture(target).as_json()
    mutation_seen = []
    real_fsync = caddy_sync.os.fsync
    count = 0

    def fail_directory_fsync(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic include directory fsync failure")
        return real_fsync(fd)

    def valid(_args, check):
        assert check is True

    with monkeypatch.context() as patch:
        patch.setattr(caddy_sync.os, "fsync", fail_directory_fsync)
        with pytest.raises(OSError, match="include directory fsync failure"):
            caddy_sync.install(source, target, caddyfile, runner=valid, on_replace=lambda: mutation_seen.append(True))

    assert mutation_seen == [True]
    assert target.read_text() == source.read_text()
    assert caddy_sync.restore(snapshot, target, caddyfile, runner=valid) is True
    assert target.read_text() == "old cloudflare config\n"


def test_release_stager_does_not_install_root_activation_control_plane():
    repository = Path(__file__).resolve().parents[1]
    stager = (repository / "scripts/stage_release.py").read_text()

    assert "/usr/local/libexec/xeonolab-activate" not in stager
    assert "/usr/local/libexec/caddy_sync.py" not in stager
    assert "/etc/xeonolab/activation.json" not in stager


def test_snapshot_encoding_rejects_malformed_metadata(tmp_path):
    _, target, _ = make_layout(tmp_path)
    snapshot = caddy_sync.capture(target)
    assert caddy_sync.Snapshot.from_json(snapshot.as_json()) == snapshot
    with pytest.raises(ValueError, match="snapshot metadata"):
        caddy_sync.Snapshot.from_json({"content": "b2xk", "uid": -1, "gid": 0, "mode": 0o644})
