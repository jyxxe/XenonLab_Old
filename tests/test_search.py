import sqlite3

from fastapi.testclient import TestClient
from tokenizers import Tokenizer, models, pre_tokenizers

from xeonolab import search
from xeonolab.services import create_app

from .conftest import auth

VOCAB = {
    "[UNK]": 0,
    "[PATH]": 1,
    "[TABLE]": 2,
    "[COLUMN]": 3,
    "[TEXT]": 4,
    "/数学/分析": 5,
    "一致收敛": 6,
    "body": 7,
}


def tiny_tokenizer(tmp_path):
    path = tmp_path / "tokenizer.json"
    tokenizer = Tokenizer(models.WordLevel(vocab=VOCAB, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.save(str(path))
    return path, tokenizer


def words(count):
    return " ".join(f"w{index}" for index in range(count))


def test_chunker_budget_overlap_and_context(tmp_path):
    _, tokenizer = tiny_tokenizer(tmp_path)
    chunks = list(search.chunk_documents(tokenizer, {"body": words(400)}, "/数学/分析", "一致收敛"))
    assert len(chunks) == 5
    assert all(column == "body" for column, _ in chunks)
    prefix = tokenizer.encode("[PATH] /数学/分析 [TABLE] 一致收敛 [COLUMN] body [TEXT] ").ids
    assert len(prefix) == 7
    lengths = [len(tokenizer.encode(chunk).ids) for _, chunk in chunks]
    assert lengths == [len(prefix) + 108] * 4 + [len(prefix) + 32]
    assert max(lengths) <= search.CHUNK_HARD_LIMIT
    assert "[PATH] /数学/分析 [TABLE] 一致收敛 [COLUMN] body [TEXT]" in chunks[0][1]


def test_chunker_short_text_and_skipped_values(tmp_path):
    _, tokenizer = tiny_tokenizer(tmp_path)
    values = {"empty": "", "blank": "   ", "number": 5, "body": "hello"}
    chunks = list(search.chunk_documents(tokenizer, values, "/x", "t"))
    assert len(chunks) == 1 and chunks[0][0] == "body"
    single = list(search.chunk_documents(tokenizer, {"body": words(108)}, "/x", "t"))
    assert len(single) == 1
    one_more = list(search.chunk_documents(tokenizer, {"body": words(109)}, "/x", "t"))
    assert len(one_more) == 2
    assert len(one_more[1][1]) < len(one_more[0][1])


def test_chunk_tokens_enforces_hard_limit(tmp_path):
    _, tokenizer = tiny_tokenizer(tmp_path)
    prefix = tokenizer.encode(" ".join(f"p{index}" for index in range(30))).ids
    chunks = list(search.chunk_tokens(tokenizer, words(50), prefix))
    assert chunks
    assert all(len(tokenizer.encode(chunk).ids) <= search.CHUNK_HARD_LIMIT for chunk in chunks)


class FakeEmbeddingResponse:
    status_code = 200

    def json(self):
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}], "model_revision": "fake-revision"}


class FakeEmbeddingClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return FakeEmbeddingResponse()


def test_reindex_stores_chunker_version_and_gates_stale_vectors(settings, tmp_path, monkeypatch):
    path, _ = tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(search.httpx, "Client", FakeEmbeddingClient)
    client = TestClient(create_app("xelibrary", settings))
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/检索/测试",
            "name": "检索表",
            "columns": [{"name": "body", "type": "string", "nullable": False}],
        },
    )
    table_id = created.json()["data"]["table_id"]
    client.post(
        f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "alpha beta gamma"}}
    )
    reindex = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": table_id})
    assert reindex.status_code == 202
    job = client.get(f"/api/v1/jobs/{reindex.json()['data']['job_id']}", headers=auth("admin"))
    assert job.json()["data"]["status"] == "completed"
    database = settings.data_dir / "tables" / f"{table_id}.xl"
    with sqlite3.connect(database) as db:
        versions = {row[0] for row in db.execute("SELECT chunker_version FROM __xl_vectors")}
    assert versions == {search.CHUNKER_VERSION}
    found = client.post("/api/v1/search/semantic", headers=auth(), json={"query": "alpha", "path": "/**"})
    results = found.json()["data"]
    assert len(results) == 1 and results[0]["score_type"] == "d100m_cosine"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE __xl_vectors SET chunker_version='chunker-v0'")
        db.commit()
    stale = client.post("/api/v1/search/semantic", headers=auth(), json={"query": "alpha", "path": "/**"})
    assert stale.json()["data"] == []
    assert stale.json()["meta"]["missing_embeddings"] == 1
