"""Opt-in real D100M HTTP/index integration smoke test, no production credentials."""

import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app

model = os.environ["D100M_MODEL_PATH"]
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
endpoint = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory() as temporary:
    env = {
        **os.environ,
        "SERVICE": "embedding",
        "APP_MODE": "development",
        "DATA_DIR": temporary + "/embedding",
        "DEV_TOKENS_JSON": json.dumps(
            {"smoke": {"sub": "indexer", "scope": "embedding:read embedding:write"}}
        ),
    }
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "xeonolab.services:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for attempt in range(60):
            try:
                if httpx.get(endpoint + "/health/ready", trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if worker.poll() is not None:
                raise RuntimeError("embedding worker failed to start")
            time.sleep(0.5)
        else:
            raise RuntimeError("embedding startup timed out")
        os.environ.update(
            EMBEDDING_URL=endpoint,
            EMBEDDING_SERVICE_TOKEN="smoke",
            D100M_TOKENIZER_PATH=model + "/tokenizer/tokenizer.json",
        )
        settings = Settings(
            Path(temporary) / "library",
            "development",
            dev_tokens={
                "admin": {
                    "sub": "smoke",
                    "scope": "xelibrary:admin xelibrary:read xelibrary:write xelibrary:query",
                }
            },
        )
        headers = {"Authorization": "Bearer admin"}
        with TestClient(create_app("xelibrary", settings)) as library:
            tid = library.post(
                "/api/v1/tables",
                headers=headers,
                json={
                    "path": "/数学/分析",
                    "name": "smoke",
                    "columns": [{"name": "body", "type": "text", "nullable": False}],
                },
            ).json()["data"]["table_id"]
            row = library.post(
                f"/api/v1/tables/{tid}/rows",
                headers=headers,
                json={"values": {"body": "一致收敛允许在适当条件下交换极限和积分。"}},
            ).json()["data"]
            job = library.post("/api/v1/index/reindex", headers=headers, json={"table_id": tid})
            assert job.status_code == 202, job.text
            jid = job.json()["data"]["job_id"]
            assert library.get(f"/api/v1/jobs/{jid}", headers=headers).json()["data"]["status"] == "completed"
            found = library.post(
                "/api/v1/search/semantic", headers=headers, json={"query": "一致收敛与积分交换"}
            )
            assert found.status_code == 200 and len(found.json()["data"]) == 1, found.text
            assert found.json()["meta"]["partial"] is False
            library.delete(
                f"/api/v1/tables/{tid}/rows/{row['_row_id']}", headers={**headers, "If-Match": '"revision:1"'}
            )
            found = library.post(
                "/api/v1/search/semantic", headers=headers, json={"query": "一致收敛与积分交换"}
            )
            assert found.json()["data"] == [], found.text
            vectors = httpx.post(
                endpoint + "/v1/embeddings",
                headers={"Authorization": "Bearer smoke"},
                json={"input": ["hello", "你好"]},
                timeout=60,
                trust_env=False,
            ).json()["data"]
            assert all(
                len(v["embedding"]) == 256 and abs(math.sqrt(sum(x * x for x in v["embedding"])) - 1) < 1e-5
                for v in vectors
            )
            print(
                "PASS: real D100M HTTP → token chunks → index → search → deletion filtering; 256D unit vectors"
            )
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
