import os
import pathlib

import pytest

from scripts import prune_releases


def make_releases(tmp_path: pathlib.Path, count: int, active_index: int = 0) -> tuple[pathlib.Path, pathlib.Path]:
    root = tmp_path / "releases"
    root.mkdir()
    names = []
    for index in range(count):
        release = root / f"{index:040x}"
        release.mkdir()
        (release / "marker").write_text(str(index))
        # Deterministic order: higher index is newer.
        os.utime(release, (1_700_000_000 + index, 1_700_000_000 + index))
        names.append(release)
    current = tmp_path / "current"
    current.symlink_to(names[active_index])
    return root, current


def test_plan_keeps_the_active_release_and_the_requested_previous_ones(tmp_path):
    root, current = make_releases(tmp_path, 8, active_index=5)
    plan = prune_releases.plan_prune(root, current, keep_previous=3)
    assert plan["active"] == root / f"{5:040x}"
    kept = {entry.name for entry in plan["keep"]}
    deleted = {entry.name for entry in plan["delete"]}
    assert kept == {f"{index:040x}" for index in (7, 6, 5, 4)}, "current plus the three newest"
    assert deleted == {f"{index:040x}" for index in (0, 1, 2, 3)}


def test_plan_keeps_the_active_release_even_when_it_is_old(tmp_path):
    root, current = make_releases(tmp_path, 6, active_index=0)
    plan = prune_releases.plan_prune(root, current, keep_previous=1)
    kept = {entry.name for entry in plan["keep"]}
    assert f"{0:040x}" in kept, "an old but still active release must never be pruned"
    assert kept == {f"{index:040x}" for index in (5, 0)}


def test_dry_run_never_deletes_and_apply_deletes_exactly_the_plan(tmp_path):
    root, current = make_releases(tmp_path, 6, active_index=5)
    plan = prune_releases.plan_prune(root, current, keep_previous=2)
    assert all(entry.exists() for entry in plan["delete"]), "plan_prune is read-only"
    removed = prune_releases.apply_prune(plan)
    assert removed == 3
    assert {entry.name for entry in root.iterdir()} == {f"{index:040x}" for index in (3, 4, 5)}


def test_symlinks_and_files_in_the_release_root_are_ignored(tmp_path):
    root, current = make_releases(tmp_path, 4, active_index=3)
    (root / "not-a-release.txt").write_text("keep me")
    (root / "linked-release").symlink_to(root / f"{0:040x}")
    plan = prune_releases.plan_prune(root, current, keep_previous=0)
    assert {entry.name for entry in plan["delete"]} == {f"{index:040x}" for index in (0, 1, 2)}
    prune_releases.apply_prune(plan)
    assert (root / "not-a-release.txt").exists()
    assert (root / "linked-release").is_symlink()


def test_current_release_outside_the_root_is_refused(tmp_path):
    root, _ = make_releases(tmp_path, 3)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    current = tmp_path / "current-outside"
    current.symlink_to(outside)
    with pytest.raises(prune_releases.PruneError):
        prune_releases.plan_prune(root, current, keep_previous=1)


def test_missing_release_root_and_negative_keep_are_refused(tmp_path):
    with pytest.raises(prune_releases.PruneError):
        prune_releases.plan_prune(tmp_path / "missing", tmp_path / "current", keep_previous=1)
    root, current = make_releases(tmp_path, 2)
    with pytest.raises(prune_releases.PruneError):
        prune_releases.plan_prune(root, current, keep_previous=-1)


def test_cli_dry_run_prints_the_plan_and_apply_removes(tmp_path, capsys):
    root, current = make_releases(tmp_path, 5, active_index=4)
    assert prune_releases.main(["--root", str(root), "--current", str(current), "--keep", "1"]) == 0
    output = capsys.readouterr().out
    assert "dry run" in output
    assert len(list(root.iterdir())) == 5
    assert prune_releases.main(
        ["--root", str(root), "--current", str(current), "--keep", "1", "--apply"]
    ) == 0
    assert {entry.name for entry in root.iterdir()} == {f"{index:040x}" for index in (3, 4)}
