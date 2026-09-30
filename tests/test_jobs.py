from xeonolab.jobs import JobStore


def test_restart_preserves_completed_and_marks_interrupted(tmp_path):
    path = tmp_path / "catalogue.sqlite"
    store = JobStore(path)
    store.create("done", "alice", "table-a")
    store.update("done", status="completed")
    store.create("pending", "bob", "table-b")
    store.update("pending", status="running")
    restarted = JobStore(path)
    assert restarted.get("done")["status"] == "completed"
    interrupted = restarted.get("pending")
    assert interrupted["status"] == "failed"
    assert interrupted["owner"] == "bob"
    assert "interrupted" in interrupted["error"]
    assert restarted.get("missing") is None
