import threading
from pathlib import Path

from fastapi.testclient import TestClient

from xeonolab.common import APIError, Settings
from xeonolab.embedding_requests import FINISHED_HISTORY, EmbeddingRequestStore
from xeonolab.services import create_app


def auth(token="alice"):
    return {"Authorization": f"Bearer {token}"}


def make_settings(tmp_path):
    return Settings(
        Path(tmp_path),
        "development",
        dev_tokens={
            "alice": {"sub": "alice", "scope": "dev:read embedding:write", "agent_kind": "codex"},
            "reader": {"sub": "reader", "scope": "xelibrary:read", "agent_kind": "codex"},
        },
    )


class StubModel:
    """Stands in for D100M so queue bookkeeping is testable without torch."""

    def __init__(self, fail_with=None, busy=False):
        self.revision = "stub-revision"
        self.lock = threading.Lock()
        self.fail_with = fail_with
        self.busy = busy

    def encode(self, texts, truncation="reject"):
        if not 1 <= len(texts) <= 32 or any(not text.strip() for text in texts):
            raise APIError(422, "INVALID_INPUT", "1–32 nonempty texts required")
        if self.busy or not self.lock.acquire(blocking=False):
            raise APIError(429, "EMBEDDING_BUSY", "D100M worker busy; retry later")
        try:
            if self.fail_with is not None:
                raise self.fail_with
            return [[0.0] * 4 for _ in texts], sum(len(text) for text in texts)
        finally:
            self.lock.release()


def queue_client(tmp_path, model=None):
    app = create_app("embedding", make_settings(tmp_path))
    if model is not None:
        app.state.model = model
    return TestClient(app)


def test_endpoints_require_dev_read(tmp_path):
    client = queue_client(tmp_path)
    assert client.get("/api/v1/embedding/queue").status_code == 401
    assert client.get("/api/v1/embedding/requests").status_code == 401
    assert client.get("/api/v1/embedding/queue", headers=auth("reader")).status_code == 403
    assert client.get("/api/v1/embedding/requests", headers=auth("reader")).status_code == 403


def test_queue_reports_worker_without_history(tmp_path):
    client = queue_client(tmp_path)
    body = client.get("/api/v1/embedding/queue", headers=auth()).json()["data"]
    assert body == {
        "active": [],
        "counts": {"running": 0, "completed": 0, "failed": 0},
        "worker": {"configured": False, "busy": False, "revision": None},
    }
    assert client.get("/api/v1/embedding/requests", headers=auth()).json()["data"] == []


def test_completed_request_is_recorded(tmp_path):
    client = queue_client(tmp_path, StubModel())
    response = client.post(
        "/v1/embeddings", headers=auth(), json={"model": "fast", "input": ["你好", "world"]}
    )
    assert response.status_code == 200
    body = client.get("/api/v1/embedding/queue", headers=auth()).json()["data"]
    assert body["counts"] == {"running": 0, "completed": 1, "failed": 0}
    assert body["worker"] == {"configured": True, "busy": False, "revision": "stub-revision"}
    history = client.get("/api/v1/embedding/requests", headers=auth()).json()["data"]
    assert len(history) == 1
    row = history[0]
    assert row["status"] == "completed"
    assert row["model"] == "d100m"
    assert row["batch_size"] == 2
    assert row["input_chars"] == 7
    assert row["tokens"] == 7
    assert isinstance(row["duration_ms"], int)
    assert row["caller"] == "alice"
    assert row["error_code"] is None


def test_failed_and_busy_requests_are_recorded(tmp_path):
    client = queue_client(tmp_path, StubModel(fail_with=APIError(422, "TOKEN_LIMIT", "too long")))
    assert (
        client.post("/v1/embeddings", headers=auth(), json={"model": "fast", "input": "x" * 40000}).status_code
        == 422
    )
    client.close()
    client = queue_client(tmp_path, StubModel(busy=True))
    assert client.post("/v1/embeddings", headers=auth(), json={"input": "hello"}).status_code == 429
    rows = client.get("/api/v1/embedding/requests", headers=auth()).json()["data"]
    assert {row["error_code"] for row in rows} == {"TOKEN_LIMIT", "EMBEDDING_BUSY"}
    assert all(row["status"] == "failed" for row in rows)
    counts = client.get("/api/v1/embedding/queue", headers=auth()).json()["data"]["counts"]
    assert counts["failed"] == 2


def test_unconfigured_and_unknown_models_are_not_recorded(tmp_path):
    client = queue_client(tmp_path)
    assert client.post("/v1/embeddings", headers=auth(), json={"model": "fast", "input": "hi"}).status_code == 503
    assert client.post("/v1/embeddings", headers=auth(), json={"model": "mystery", "input": "hi"}).status_code == 422
    assert client.get("/api/v1/embedding/requests", headers=auth()).json()["data"] == []


def test_history_pagination_filters_and_validates(tmp_path):
    client = queue_client(tmp_path, StubModel())
    for index in range(5):
        assert (
            client.post("/v1/embeddings", headers=auth(), json={"model": "fast", "input": f"t{index}"}).status_code
            == 200
        )
    assert client.post("/v1/embeddings", headers=auth(), json={"input": ""}).status_code == 422
    page = client.get("/api/v1/embedding/requests?limit=2", headers=auth()).json()
    assert len(page["data"]) == 2
    assert page["data"][0]["id"] > page["data"][1]["id"]
    assert page["meta"]["next_cursor"]
    follow = client.get(
        f"/api/v1/embedding/requests?limit=2&cursor={page['meta']['next_cursor']}", headers=auth()
    ).json()
    assert follow["data"][0]["id"] < page["data"][-1]["id"]
    only_completed = client.get(
        "/api/v1/embedding/requests?status=completed&limit=100", headers=auth()
    ).json()["data"]
    assert len(only_completed) == 5
    assert all(row["status"] == "completed" for row in only_completed)
    assert client.get("/api/v1/embedding/requests?limit=0", headers=auth()).status_code == 422
    assert client.get("/api/v1/embedding/requests?limit=1001", headers=auth()).status_code == 422
    assert client.get("/api/v1/embedding/requests?status=running", headers=auth()).status_code == 422
    assert client.get("/api/v1/embedding/requests?cursor=abc", headers=auth()).status_code == 422


def test_history_prunes_finished_rows_but_keeps_running(tmp_path):
    store = EmbeddingRequestStore(Path(tmp_path) / "queue.sqlite3")
    for _ in range(FINISHED_HISTORY + 25):
        request_id = store.start("d100m", 1, 1, "reject", "alice")
        store.finish(request_id, "completed", tokens=1)
    assert store.counts()["completed"] == FINISHED_HISTORY
    rows, _ = store.history(limit=1000)
    assert len(rows) == FINISHED_HISTORY
    assert rows[0]["id"] > rows[-1]["id"]
    running = store.start("d100m", 1, 1, "reject", "alice")
    assert [row["id"] for row in store.active()] == [running]
    store.finish(store.start("d100m", 1, 1, "reject", "alice"), "failed", error_code="EMBEDDING_BUSY")
    assert [row["id"] for row in store.active()] == [running]
    assert store.counts() == {"running": 1, "completed": FINISHED_HISTORY - 1, "failed": 1}


def test_restart_marks_stranded_running_rows_failed(tmp_path):
    database = Path(tmp_path) / "queue.sqlite3"
    store = EmbeddingRequestStore(database)
    store.start("d100m", 1, 4, "reject", "alice")
    reopened = EmbeddingRequestStore(database)
    assert reopened.active() == []
    counts = reopened.counts()
    assert counts["running"] == 0 and counts["failed"] == 1
    rows, _ = reopened.history(limit=10)
    assert rows[0]["error_code"] == "WORKER_RESTARTED"
