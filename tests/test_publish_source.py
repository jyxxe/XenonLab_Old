import subprocess

from scripts import publish_source


def fake_git(monkeypatch, current="feature/current", branches=None, push_status=0):
    branches = {current} if branches is None else set(branches)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        result = None
        if command == ["git", "branch", "--show-current"]:
            result = subprocess.CompletedProcess(command, 0, stdout=current)
        elif command[:3] == ["git", "check-ref-format", "--branch"]:
            name = command[3]
            valid = bool(name) and not any(character.isspace() for character in name)
            result = subprocess.CompletedProcess(command, 0 if valid else 1, stdout=name if valid else "")
        elif command[:4] == ["git", "show-ref", "--verify", "--quiet"]:
            name = command[4].removeprefix("refs/heads/")
            result = subprocess.CompletedProcess(command, 0 if name in branches else 1)
        elif command[:2] == ["git", "push"]:
            result = subprocess.CompletedProcess(command, push_status)
        else:
            raise AssertionError(f"unexpected git command: {command}")
        if kwargs.get("check") and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command)
        return result

    monkeypatch.setattr(publish_source.subprocess, "run", run)
    return calls


def test_default_publish_pushes_only_current_branch(monkeypatch, tmp_path):
    secrets = tmp_path / "runtime" / "deploy-secrets"
    secrets.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    calls = fake_git(monkeypatch)

    assert publish_source.main([]) == 0

    pushes = [command for command, _ in calls if command[:2] == ["git", "push"]]
    assert pushes == [
        [
            "git",
            "push",
            "https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab.git",
            "refs/heads/feature/current:refs/heads/feature/current",
        ]
    ]
    assert not (secrets / "forgejo-askpass.py").exists()


def test_explicit_branch_and_local_tls_are_preserved(monkeypatch, tmp_path):
    secrets = tmp_path / "runtime" / "deploy-secrets"
    secrets.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    calls = fake_git(monkeypatch, branches={"feature/current", "release/candidate"})

    assert publish_source.main(["--branch", "release/candidate", "--local"]) == 0

    push, environment = next(
        (command, kwargs["env"])
        for command, kwargs in calls
        if command[:2] == ["git", "push"]
    )
    assert push == [
        "git",
        "push",
        "https://192.168.0.101:9445/xenon-admin/XeonoLab.git",
        "refs/heads/release/candidate:refs/heads/release/candidate",
    ]
    assert environment["GIT_SSL_CAINFO"] == str(secrets / "lan-root-ca.crt")
    assert not (secrets / "forgejo-askpass.py").exists()


def test_detached_and_invalid_or_missing_branches_fail_before_askpass(monkeypatch, tmp_path, capsys):
    secrets = tmp_path / "runtime" / "deploy-secrets"
    secrets.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    def detached(command, **kwargs):
        assert command == ["git", "branch", "--show-current"]
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(publish_source.subprocess, "run", detached)
    assert publish_source.main([]) == 2
    assert "detached HEAD" in capsys.readouterr().err
    assert not (secrets / "forgejo-askpass.py").exists()

    fake_git(monkeypatch, branches={"feature/current"})
    assert publish_source.main(["--branch", "missing/branch"]) == 2
    assert "local branch does not exist" in capsys.readouterr().err
    assert not (secrets / "forgejo-askpass.py").exists()

    fake_git(monkeypatch, branches={"feature/current"})
    assert publish_source.main(["--branch", "bad branch"]) == 2
    assert "invalid branch name" in capsys.readouterr().err
    assert not (secrets / "forgejo-askpass.py").exists()


def test_askpass_is_removed_when_mock_push_fails(monkeypatch, tmp_path):
    secrets = tmp_path / "runtime" / "deploy-secrets"
    secrets.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    fake_git(monkeypatch, push_status=1)

    assert publish_source.main([]) == 1
    assert not (secrets / "forgejo-askpass.py").exists()
