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


@pytest.mark.parametrize("outcome", ["success", "rejected", "uncertain"])
def test_merge_requires_current_review_and_server_head_guard(tmp_path, monkeypatch, outcome):
    sha = "a" * 40
    state = controller.State(tmp_path)
    state.db.execute("INSERT INTO reviews VALUES(1,?,0)", (sha,))
    state.db.commit()
    calls = []

    class Fake:
        merged = False

        def __init__(self, config):
            pass

        def api(self, method, path, body=None):
            calls.append((method, path, body))
            if method == "POST" and path.endswith("/merge"):
                if outcome == "rejected":
                    raise controller.ForgejoRejected("HTTP 409")
                if outcome == "uncertain":
                    raise TimeoutError("unknown merge outcome")
                self.merged = True
                return {}
            if path == "/pulls/1":
                return {"head": {"sha": sha}, "base": {"ref": controller.RELEASE_BRANCH},
                        "state": "closed" if self.merged else "open", "mergeable": True,
                        "merged": self.merged, "merge_commit_sha": "c" * 40}
            if path.startswith("/branches/"):
                return {"protected": True, "required_approvals": 1, "enable_status_check": True,
                        "status_check_contexts": controller.REQUIRED_CHECKS}
            if path.endswith("/status"):
                return {
                    "state": "success",
                    "statuses": [
                        {
                            "context": "devagent/preflight",
                            "state": "success",
                            "creator": {"login": "devagent-validator"},
                        }
                    ] + [{"context": c, "state": "success"} for c in controller.REQUIRED_CHECKS[:-1]],
                }

        def pages(self, path):
            return [{"user": {"login": "devagent-maintainer"}, "commit_id": sha, "state": "APPROVED"}]

    monkeypatch.setattr(controller, "Forgejo", Fake)
    config = {"state_dir": str(tmp_path), "identity": "devagent-maintainer"}
    if outcome != "success":
        expected = controller.ForgejoRejected if outcome == "rejected" else TimeoutError
        with pytest.raises(expected):
            controller.devctl(config, ["merge", "1", sha])
        publication = state.db.execute("SELECT * FROM publications").fetchone()
        if outcome == "rejected":
            assert publication is None  # Refusal must not block unrelated PRs forever.
            assert state.db.execute("SELECT event FROM audit").fetchone()[0] == "forgejo.merge_rejected"
        else:
            assert publication["head"] == sha and publication["commit_sha"] is None
        return
    controller.devctl(config, ["merge", "1", sha])
    merged_call = next(c for c in calls if c[0] == "POST")
    assert merged_call[2]["head_commit_id"] == sha
    assert "force_merge" not in merged_call[2]
    publication = state.db.execute("SELECT * FROM publications").fetchone()
    assert publication["head"] == sha and publication["commit_sha"] == "c" * 40

    with pytest.raises(RuntimeError, match="exact head"):
        controller.devctl({"state_dir": str(tmp_path)}, ["merge", "1", "b" * 40])


@pytest.mark.parametrize("code,rejected", [(403, True), (409, True), (422, True), (408, False), (500, False)])
def test_forgejo_distinguishes_refusal_from_unknown_outcome(code, rejected):
    class Opener:
        def open(self, request, timeout):
            raise controller.urllib.error.HTTPError(request.full_url, code, "private response", {}, None)

    forgejo = controller.Forgejo.__new__(controller.Forgejo)
    forgejo.base = "https://forgejo.invalid/api/v1/repos/example/repo"
    forgejo.token = "test-token"
    forgejo.opener = Opener()
    with pytest.raises(RuntimeError) as caught:
        forgejo.api("POST", "/pulls/1/merge", {})
    assert isinstance(caught.value, controller.ForgejoRejected) == rejected
    assert "private response" not in str(caught.value) and "test-token" not in str(caught.value)


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
    review = json.loads(controller.runtime_environment(config, automatic=True, review=True)["OPENCODE_CONFIG_CONTENT"])
    assert review["agent"]["maintainer"]["steps"] == 24
    assert review["agent"]["maintainer"]["permission"]["edit"] == "deny"
    assert "uv run *" not in auto["permission"]["bash"]
    assert "git *" not in auto["permission"]["bash"]
    assert auto["permission"]["bash"]["*auth.json*"] == "deny"
    assert auto["permission"]["bash"]["devctl *"] == "allow"
    assert auto["permission"]["bash"]["devctl deploy *"] == "allow"
    assert auto["permission"]["bash"]["devctl merge *"] == "allow"
    assert auto["permission"]["bash"]["devctl review *"] == "allow"
    assert auto["permission"]["bash"]["devctl pr-create *"] == "deny"
    assert manual["agent"]["maintainer"]["steps"] == 48
    assert manual["agent"]["maintainer"]["permission"]["edit"] == "allow"
    assert manual["agent"]["maintainer"]["permission"]["bash"]["devctl deploy *"] == "allow"
    assert manual["agent"]["maintainer"]["permission"]["bash"]["devctl merge *"] == "allow"


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


def test_project_events_treats_missing_status_rows_as_empty_and_continues():
    prs = [
        {"number": number, "head": {"sha": str(number) * 40}, "base": {"ref": "main"},
         "title": f"PR {number}", "state": "open", "merged": False}
        for number in range(1, 5)
    ]
    combined = {
        "1" * 40: {"state": "pending", "statuses": None},
        "2" * 40: {"state": "pending"},
        "3" * 40: {"state": "pending", "statuses": []},
        "4" * 40: {"state": "success", "statuses": [
            {"context": "ci/test", "status": "success", "description": "passed",
             "target_url": "https://ci.invalid/run/4", "ignored": "not exposed"}
        ]},
    }

    class Fake:
        def api(self, method, path):
            if path.startswith("/pulls?"):
                return prs
            return combined[path.split("/")[2]]

        def pages(self, path):
            return []

    events = list(controller.project_events(Fake(), {"identity": "devagent-maintainer"}))
    assert [resource for resource, _ in events] == ["pr:1", "pr:2", "pr:3", "pr:4"]
    assert [payload["ci"]["statuses"] for _, payload in events[:3]] == [[], [], []]
    assert events[0][1]["ci"]["state"] == "pending"
    assert events[3][1]["ci"] == {
        "state": "success",
        "statuses": [{"context": "ci/test", "status": "success", "description": "passed",
                      "target_url": "https://ci.invalid/run/4"}],
    }


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
    "reason,steps,expected", [("tool-calls", 1, "failed"), ("stop", 1, "done"), ("stop", 12, "done"), ("stop", 24, "failed"), ("stop", 32, "failed")]
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


@pytest.mark.parametrize('case', [
    'wrong_target', 'draft', 'changed_head', 'closed', 'conflict', 'unprotected',
    'missing_required_context', 'failed_ci', 'missing_preflight', 'wrong_validator',
    'dismissed_approval', 'stale_approval', 'later_rejection', 'other_rejection',
])
def test_invalid_pr_never_reaches_merge_endpoint(tmp_path, monkeypatch, case):
    sha = 'a' * 40
    state = controller.State(tmp_path)
    state.db.execute('INSERT INTO reviews VALUES(1,?,0)', (sha,))
    state.db.commit()

    class Fake:
        def __init__(self, config):
            pass

        def api(self, method, path, body=None):
            assert method == 'GET', 'Rejected PR reached mutation endpoint'
            if path == '/pulls/1':
                return {'head': {'sha': 'b' * 40 if case == 'changed_head' else sha},
                        'base': {'ref': 'main' if case == 'wrong_target' else controller.RELEASE_BRANCH},
                        'draft': case == 'draft', 'state': 'closed' if case == 'closed' else 'open',
                        'mergeable': case != 'conflict'}
            if path.startswith('/branches/'):
                return {'protected': case != 'unprotected', 'required_approvals': 1,
                        'enable_status_check': True, 'status_check_contexts': controller.REQUIRED_CHECKS}
            statuses = [{'context': c, 'state': 'success'} for c in controller.REQUIRED_CHECKS[:-1]]
            if case == 'missing_required_context':
                statuses = statuses[:1]
            if case != 'missing_preflight':
                statuses.append({'context': 'devagent/preflight', 'state': 'success',
                                 'creator': {'login': 'other' if case == 'wrong_validator' else 'devagent-validator'}})
            return {'state': 'failure' if case == 'failed_ci' else 'success', 'statuses': statuses}

        def pages(self, path):
            rows = [{'id': 1, 'user': {'login': 'devagent-maintainer'}, 'commit_id': sha,
                     'state': 'APPROVED', 'dismissed': case == 'dismissed_approval',
                     'stale': case == 'stale_approval'}]
            if case in ('later_rejection', 'other_rejection'):
                rows.append({'id': 2, 'user': {'login': 'other' if case == 'other_rejection' else 'devagent-maintainer'},
                             'commit_id': sha, 'state': 'REQUEST_CHANGES'})
            return rows

    monkeypatch.setattr(controller, 'Forgejo', Fake)
    with pytest.raises(RuntimeError):
        controller.devctl({'state_dir': str(tmp_path), 'identity': 'devagent-maintainer'}, ['merge', '1', sha])
    assert state.db.execute('SELECT count(*) FROM publications').fetchone()[0] == 0


def test_merge_rejects_success_state_without_status_rows(tmp_path, monkeypatch):
    sha = 'a' * 40
    state = controller.State(tmp_path)
    state.db.execute('INSERT INTO reviews VALUES(1,?,0)', (sha,))
    state.db.commit()

    class Fake:
        def __init__(self, config):
            pass

        def api(self, method, path, body=None):
            assert method == 'GET', 'missing CI rows must never reach the merge endpoint'
            if path == '/pulls/1':
                return {'head': {'sha': sha}, 'base': {'ref': controller.RELEASE_BRANCH},
                        'state': 'open', 'mergeable': True, 'merged': False}
            if path.startswith('/branches/'):
                return {'protected': True, 'required_approvals': 1, 'enable_status_check': True,
                        'status_check_contexts': controller.REQUIRED_CHECKS}
            return {'state': 'success', 'statuses': None}

        def pages(self, path):
            return [{'user': {'login': 'devagent-maintainer'}, 'commit_id': sha, 'state': 'APPROVED'}]

    monkeypatch.setattr(controller, 'Forgejo', Fake)
    with pytest.raises(RuntimeError, match='all required current-head checks'):
        controller.devctl({'state_dir': str(tmp_path), 'identity': 'devagent-maintainer'},
                          ['merge', '1', sha])
    assert state.db.execute('SELECT count(*) FROM publications').fetchone()[0] == 0


def test_merge_intent_recovers_deployment_and_terminal_result_without_polling_model(tmp_path, monkeypatch):
    state = controller.State(tmp_path)
    sha = 'c' * 40
    state.db.execute('INSERT INTO publications(number,head) VALUES(1,?)', ('a' * 40,))
    state.db.commit()

    class Fake:
        def api(self, method, path):
            if path.startswith('/pulls/'):
                return {'merged': True, 'head': {'sha': 'a' * 40},
                        'base': {'ref': controller.RELEASE_BRANCH}, 'merge_commit_sha': sha}
            return {'commit': {'id': sha}}

    resource, payload = list(controller.publication_events(Fake(), state))[0]
    assert resource == 'publication:1' and payload['state'] == 'ready'
    assert state.observe(resource, payload)
    assert not state.observe('pr:1', {'state': 'closed'}, baseline=True)
    assert state.pending()['resource'] == resource  # Closed PR suppression does not lose deployment work.
    state.db.execute('UPDATE publications SET operation_id=?', ('d' * 32,))
    state.db.commit()
    monkeypatch.setattr(controller, 'protected_request', lambda *a: {'state': 'running'})
    assert list(controller.publication_events(Fake(), state)) == []
    monkeypatch.setattr(controller, 'protected_request', lambda *a: {
        'state': 'completed', 'result': 'completed', 'verification': {'101': {'ok': True}}})
    resource, payload = list(controller.publication_events(Fake(), state))[0]
    assert payload['operation']['verification']['101']['ok']
    assert state.observe(resource, payload)
    monkeypatch.setattr(controller, 'protected_request', lambda *a: pytest.fail('Completed operation repolled'))
    assert not state.observe(*list(controller.publication_events(Fake(), state))[0])
    assert state.pending()['payload'] == json.dumps(payload)


def test_superseded_merged_commit_is_not_a_deployment_candidate(tmp_path):
    state = controller.State(tmp_path)
    state.db.execute('INSERT INTO publications(number,head,commit_sha) VALUES(1,?,?)', ('a' * 40, 'b' * 40))
    state.db.commit()

    class Fake:
        def api(self, method, path):
            if path.startswith('/pulls/'):
                return {'merged': True, 'head': {'sha': 'a' * 40}, 'base': {'ref': controller.RELEASE_BRANCH},
                        'merge_commit_sha': 'b' * 40}
            return {'commit': {'id': 'c' * 40}}

    assert list(controller.publication_events(Fake(), state))[0][1]['state'] == 'superseded'


@pytest.mark.parametrize('operation_state', ['running', 'completed', 'failed', 'interrupted'])
def test_repeated_deploy_uses_recorded_operation_instead_of_resubmission(tmp_path, monkeypatch, capsys, operation_state):
    state = controller.State(tmp_path)
    state.db.execute('INSERT INTO publications(number,head,commit_sha,operation_id) VALUES(1,?,?,?)',
                     ('a' * 40, 'b' * 40, 'c' * 32))
    state.db.commit()
    monkeypatch.setattr(controller, 'Forgejo', lambda _: object())
    calls = []

    def protected(action, args):
        calls.append((action, args))
        return {'id': args[0], 'state': operation_state}

    monkeypatch.setattr(controller, 'protected_request', protected)
    controller.devctl({'state_dir': str(tmp_path)}, ['deploy', controller.REPOSITORY, 'b' * 40, 'production'])
    assert calls == [('operation', ['c' * 32])]
    assert json.loads(capsys.readouterr().out)['state'] == operation_state
