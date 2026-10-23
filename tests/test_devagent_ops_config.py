"""Contract: versioned operational hardening for the DevAgent deployment path.

Two incidents drove these files:

* 2026-10-22 unattended-upgrades: the needrestart APT hook restarted a running
  ``devagent-operation-*`` transient unit after activation had completed and the
  broker marked the deployment interrupted. The override that prevents this is
  installed ad hoc on the DevAgent host; it must stay versioned so a rebuilt host
  is hardened the same way.
* 2026-10-22 VM 107 disk exhaustion: staging failed because the release
  directories had never been pruned. The retention policy and the pruning tool
  must stay documented and covered by tests.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NEEDRESTART = REPO_ROOT / "infra" / "devagent" / "needrestart-transient.conf"
RETENTION_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "deployment-disk-retention.md"
PRUNE_SCRIPT = REPO_ROOT / "scripts" / "prune_releases.py"


def test_needrestart_override_protects_transient_devagent_units():
    text = NEEDRESTART.read_text(encoding="utf-8")
    assert "$nrconf{override_rc}{qr(^devagent-operation-)} = 0;" in text
    assert "$nrconf{override_rc}{qr(^devagent-preflight-)} = 0;" in text
    assert "/etc/needrestart/conf.d/devagent-transient.conf" in text, "document the install path"
    # The file must explain why the override exists, not just contain perl.
    assert "transient" in text.lower() and "deployment" in text.lower()


def test_retention_runbook_documents_policy_and_verification():
    text = RETENTION_RUNBOOK.read_text(encoding="utf-8")
    assert "three newest previous releases" in text
    assert "/opt/xeonolab/current" in text and "/opt/xeonolab/releases" in text
    assert "prune_releases.py" in text, "reference the pruning tool"
    assert "--apply" in text and "dry" in text.lower(), "require a dry run before deleting"
    assert "5 GiB" in text and "1 GiB" in text, "state the free-space thresholds"
    assert "VM 107" in text, "record the incident"
    assert "needrestart-transient.conf" in text


def test_pruning_tool_exists_and_defaults_to_a_dry_run():
    text = PRUNE_SCRIPT.read_text(encoding="utf-8")
    assert "def plan_prune(" in text and "def apply_prune(" in text
    assert '"--apply"' in text and "dry run" in text.lower()
    assert "refusing to delete the active release" in text
