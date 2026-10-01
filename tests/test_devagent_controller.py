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


@pytest.mark.parametrize("baseline", [False, True])
def test_new_event_supersedes_failed_retry_and_old_queue(tmp_path, monkeypatch, baseline):
    state = controller.State(tmp_path)
    state.observe("pr:89", {"head": "old"})
    old = state.pending()["id"]
    state.db.execute("UPDATE tasks SET status='failed',attempts=1,updated=0 WHERE id=?", (old,))
    state.db.commit()
    state.observe("pr:90", {"head": "unrelated"})
    state.observe("pr:89", {"head": "fresh"}, baseline=baseline)
    assert state.db.execute("SELECT status FROM tasks WHERE id=?", (old,)).fetchone()[0] == "superseded"
    assert state.db.execute("SELECT status FROM tasks WHERE resource='pr:90'").fetchone()[0] == "pending"
    fresh = state.db.execute("SELECT id FROM tasks WHERE resource='pr:89' AND status='pending'").fetchall()
    assert len(fresh) == (0 if baseline else 1)
    state.db.execute("UPDATE tasks SET status='done' WHERE status='pending'")
    state.db.commit()
    monkeypatch.setattr(controller.time, "time", lambda: 99999999)
    assert state.pending() is None  # expired retry cooldown cannot resurrect the old PR head


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


def test_automatic_coordination_preserves_manual_capabilities(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"agent": {"maintainer": {"steps": 48, "permission": {
        "edit": "allow", "bash": {"*": "deny", "git *": "allow", "uv run *": "allow",
                                  "*auth.json*": "deny"}}}}}))
    config = {"policy_file": str(policy)}
    automatic = json.loads(controller.runtime_environment(config, automatic=True)["OPENCODE_CONFIG_CONTENT"])
    manual = json.loads(controller.runtime_environment(config)["OPENCODE_CONFIG_CONTENT"])
    auto = automatic["agent"]["maintainer"]
    assert auto["steps"] == 12 and auto["permission"]["edit"] == "deny"
    assert "uv run *" not in auto["permission"]["bash"]
    assert "git *" not in auto["permission"]["bash"]
    assert auto["permission"]["bash"]["*auth.json*"] == "deny"
    assert auto["permission"]["bash"]["devctl *"] == "allow"
    assert auto["permission"]["bash"]["devctl deploy *"] == "deny"
    assert auto["permission"]["bash"]["devctl merge *"] == "deny"
    assert auto["permission"]["bash"]["devctl review *"] == "deny"
    assert auto["permission"]["bash"]["devctl pr-create *"] == "deny"
    assert manual["agent"]["maintainer"]["steps"] == 48
    assert manual["agent"]["maintainer"]["permission"]["edit"] == "allow"
    assert manual["agent"]["maintainer"]["permission"]["bash"]["devctl deploy *"] == "deny"
    assert manual["agent"]["maintainer"]["permission"]["bash"]["devctl merge *"] == "deny"


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


def test_closed_pr_events_supersede_old_work_without_starting_model(tmp_path):
    state = controller.State(tmp_path)
    state.observe("pr:97", {"state": "open", "head": "a" * 40})
    old_task = state.pending()["id"]

    closed = {"state": "closed", "merged": True, "merge_commit_sha": "c" * 40}
    assert state.observe("pr:97", closed)
    assert state.db.execute("SELECT status FROM tasks WHERE id=?", (old_task,)).fetchone()[0] == "superseded"
    assert state.pending() is None

    # A previous release could have persisted an already-closed event as pending work.
    state.db.execute(
        "INSERT INTO tasks(id,resource,fingerprint,payload,status,updated) VALUES(?,?,?,?,?,?)",
        ("stale-closed-pr", "pr:97", controller.digest(closed), json.dumps(closed), "failed", 0),
    )
    state.db.commit()
    assert not state.observe("pr:97", closed)
    assert state.db.execute(
        "SELECT status FROM tasks WHERE id='stale-closed-pr'"
    ).fetchone()[0] == "superseded"
    assert state.pending() is None


@pytest.mark.parametrize(
    "reason,steps,expected", [("tool-calls", 1, "failed"), ("stop", 1, "done"), ("stop", 12, "failed"), ("stop", 32, "failed")]
)
def test_cli_zero_exit_without_finished_model_is_not_success(tmp_path, monkeypatch, reason, steps, expected):
    state = controller.State(tmp_path / "state")
    state.observe("pr:1", {"head": "a"}, baseline=True)
    state.observe("pr:1", {"head": "b"})
    task = state.pending()
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    config = {"checkout": str(tmp_path), "private_logs": str(tmp_path / "logs"), "policy_file": str(policy)}
    commands = []

    class Process:
        returncode = 0

        def __init__(self, argv, **kwargs):
            commands.append(argv)
            assert argv[argv.index("--dir") + 1] == str(tmp_path)
            assert kwargs["env"]["PWD"] == str(tmp_path)
            event = {"sessionID": "independent-session", "type": "step_finish", "part": {"reason": reason}}
            kwargs["stdout"].write((json.dumps(event) + "\n") * steps)

        def communicate(self, *args, **kwargs):
            pass

    monkeypatch.setattr(controller.subprocess, "Popen", Process)
    assert controller.run_model(config, state, task) == expected
    assert state.db.execute("SELECT status FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == expected
    assert "--continue" not in commands[0]
