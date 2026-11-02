"""Contract: deployment targets prune old release directories automatically.

Issue #383: every deployment adds an immutable ~850 MiB release directory under
``/opt/xeonolab/releases`` and the deploy flow never removes old ones. On
2026-11-02 VM107 was back at 96% disk (82 releases, 69 GiB), the same failure
mode that broke a deployment on 2026-10-22. The manual runbook fix keeps
``current`` plus three previous releases; this contract keeps a daily systemd
timer, the root-only wrapper and the bootstrap installer in sync with it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = REPO_ROOT / "infra" / "systemd"


def _text(name: str) -> str:
    return (SYSTEMD / name).read_text(encoding="utf-8")


def test_prune_timer_keeps_the_documented_rollback_window():
    timer = _text("xeonolab-release-prune.timer")
    assert "OnCalendar=" in timer
    assert "Persistent=true" in timer
    assert "Unit=xeonolab-release-prune.service" in timer
    assert "WantedBy=timers.target" in timer


def test_prune_service_runs_the_release_prune_tool_as_root_wrapper():
    service = _text("xeonolab-release-prune.service")
    assert "ExecStart=/usr/local/libexec/xeonolab-release-prune" in service
    assert "ConditionPathExists=/opt/xeonolab/current/scripts/prune_releases.py" in service
    assert "Type=oneshot" in service


def test_prune_wrapper_delegates_to_the_release_tool_keeping_three():
    wrapper = _text("release_prune.sh")
    assert "keep=\"${XEONOLAB_RELEASE_KEEP:-3}\"" in wrapper
    assert 'script="$current/scripts/prune_releases.py"' in wrapper
    assert '"$script" --keep "$keep" --apply' in wrapper
    # The wrapper must never implement deletion itself: the reviewed tool owns
    # the active-release/symlink/path guards.
    assert "rm -rf" not in wrapper
    assert "shutil" not in wrapper


def test_bootstrap_installer_enables_the_timer_when_the_units_are_supplied():
    stage = _text("stage.sh")
    for name in (
        "release_prune.sh",
        "xeonolab-release-prune.service",
        "xeonolab-release-prune.timer",
    ):
        assert f"/tmp/{name}" in stage, f"stage.sh must install {name} when supplied"
    assert "systemctl enable --now xeonolab-release-prune.timer" in stage
