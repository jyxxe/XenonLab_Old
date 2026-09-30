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


def test_list_filters_orders_and_counts(tmp_path):
    store = JobStore(tmp_path / "catalogue.sqlite")
    for job_id, owner, table in [
        ("first", "alice", "table-a"),
        ("second", "bob", "table-b"),
        ("third", "alice", "table-a"),
    ]:
        store.create(job_id, owner, table)
    store.update("first", status="completed")
    store.update("third", status="running")
    jobs = store.list()
    assert [job["job_id"] for job in jobs] == ["third", "second", "first"]
    assert [job["job_id"] for job in store.list(table_id="table-a")] == ["third", "first"]
    assert [job["job_id"] for job in store.list(status="completed")] == ["first"]
    assert [job["job_id"] for job in store.list(owner="bob")] == ["second"]
    assert store.list(limit=2) and len(store.list(limit=2)) == 2
    assert store.counts() == {"queued": 1, "running": 1, "completed": 1, "failed": 0}
    assert store.counts(table_id="table-a") == {
        "queued": 0,
        "running": 1,
        "completed": 1,
        "failed": 0,
    }
