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


def test_snapshot_encoding_rejects_malformed_metadata(tmp_path):
    _, target, _ = make_layout(tmp_path)
    snapshot = caddy_sync.capture(target)
    assert caddy_sync.Snapshot.from_json(snapshot.as_json()) == snapshot
    with pytest.raises(ValueError, match="snapshot metadata"):
        caddy_sync.Snapshot.from_json({"content": "b2xk", "uid": -1, "gid": 0, "mode": 0o644})
