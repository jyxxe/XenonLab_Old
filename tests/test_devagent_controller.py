import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "infra/devagent/devagent.py"
SPEC = importlib.util.spec_from_file_location("devagent_controller", PATH)
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


def test_baseline_dedup_and_crash_recovery(tmp_path):
    state = controller.State(tmp_path)
    assert not state.observe("pr:1", {"head": "a"}, baseline=True)
    assert not state.observe("pr:1", {"head": "a"})
    assert state.pending() is None
    assert state.observe("pr:1", {"head": "b"})
    task = state.pending()
    state.db.execute("UPDATE tasks SET status='running',attempts=1 WHERE id=?", (task["id"],))
    state.db.commit()
    state.recover()
    assert state.pending() is None  # failed tasks have a cooldown, no immediate model loop
    assert state.db.execute("SELECT status FROM tasks").fetchone()[0] == "failed"
    assert not state.observe("pr:1", {"head": "b"})


def test_kernel_lock_excludes_other_process_and_recovers(tmp_path):
    state = controller.State(tmp_path)
    script = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    with state.lock():
        result = subprocess.run(
            ["python", "-c", script, str(tmp_path / "working-tree.lock")], capture_output=True
        )
        assert result.returncode != 0
    result = subprocess.run(
        ["python", "-c", script, str(tmp_path / "working-tree.lock")], capture_output=True
    )
    assert result.returncode == 0


def test_unchanged_watcher_does_not_start_model(tmp_path, monkeypatch):
    config = {"state_dir": str(tmp_path)}
    monkeypatch.setattr(controller, "Forgejo", lambda _: object())
    monkeypatch.setattr(controller, "project_events", lambda *args: [("pr:1", {"head": "a"})])
    monkeypatch.setattr(controller, "run_model", lambda *args: pytest.fail("No-change watcher called LLM"))
    controller.watcher(config, baseline=True)
    controller.watcher(config)


@pytest.mark.parametrize("sha", ["main", "a" * 39, "A" * 40, "a" * 40 + ";id"])
def test_exact_commit_is_mandatory(sha):
    with pytest.raises(ValueError):
        controller.head_sha(sha)


def test_merge_requires_current_review_and_server_head_guard(tmp_path, monkeypatch):
    sha = "a" * 40
    state = controller.State(tmp_path)
    state.db.execute("INSERT INTO reviews VALUES(1,?,0)", (sha,))
    state.db.commit()
    calls = []

    class Fake:
        def __init__(self, config):
            pass

        def api(self, method, path, body=None):
            calls.append((method, path, body))
            if path == "/pulls/1":
                return {"head": {"sha": sha}, "state": "open", "mergeable": True}
            if path.endswith("/status"):
                return {
                    "state": "success",
                    "statuses": [
                        {
                            "context": "devagent/preflight",
                            "state": "success",
                            "creator": {"login": "devagent-validator"},
                        }
                    ],
                }

        def pages(self, path):
            return [{"user": {"login": "devagent-maintainer"}, "commit_id": sha, "state": "APPROVED"}]

    monkeypatch.setattr(controller, "Forgejo", Fake)
    config = {"state_dir": str(tmp_path), "identity": "devagent-maintainer"}
    controller.devctl(config, ["merge", "1", sha])
    assert calls[-1][2]["head_commit_id"] == sha
    assert "force_merge" not in calls[-1][2]

    with pytest.raises(RuntimeError, match="exact head"):
        controller.devctl({"state_dir": str(tmp_path)}, ["merge", "1", "b" * 40])


def test_project_config_cannot_override_installed_policy(tmp_path, monkeypatch):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"agent": {"maintainer": {"steps": 32}}, "plugin": []}))
    monkeypatch.setenv("OPENCODE_CONFIG", "/malicious/repo/opencode.json")
    env = controller.runtime_environment({"policy_file": str(policy)})
    assert "OPENCODE_CONFIG" not in env
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"]["maintainer"]["steps"] == 32


def test_checkpoint_push_repository_timestamp_does_not_wake_prs():
    class Fake:
        updated = "before"

        def api(self, method, path):
            if path.startswith("/pulls?"):
                return [
                    {
                        "number": 1,
                        "head": {"sha": "a" * 40},
                        "base": {"ref": "main"},
                        "title": "PR",
                        "state": "open",
                        "merged": False,
                    }
                ]
            return {"state": "pending", "statuses": [], "repository": {"updated_at": self.updated}}

        def pages(self, path):
            return []

    forgejo = Fake()
    before = list(controller.project_events(forgejo, {"identity": "devagent-maintainer"}))
    forgejo.updated = "checkpoint"
    after = list(controller.project_events(forgejo, {"identity": "devagent-maintainer"}))
    assert before == after


def test_closed_pr_live_branch_references_do_not_wake_history():
    class Fake:
        head = "a" * 40

        def api(self, method, path):
            return [
                {
                    "number": 1,
                    "head": {"sha": self.head},
                    "state": "closed",
                    "merged": True,
                    "merge_commit_sha": "c" * 40,
                }
            ]

        def pages(self, path):
            return []

    forgejo = Fake()
    before = list(controller.project_events(forgejo, {"identity": "devagent-maintainer"}))
    forgejo.head = "b" * 40
    assert before == list(controller.project_events(forgejo, {"identity": "devagent-maintainer"}))
