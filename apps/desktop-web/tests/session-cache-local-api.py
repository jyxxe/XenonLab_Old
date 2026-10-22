"""Start an isolated XeLibrary API with two synthetic users and a private fixture table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app

TOKEN_A = "cycle5-user-a-synthetic"
TOKEN_B = "cycle5-user-b-synthetic"
SCOPES_A = "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach xelibrary:admin xelibrary:theorem:read xelibrary:theorem:propose"
SCOPES_B = "xelibrary:read xelibrary:query"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    settings = Settings(
        Path(args.data_dir),
        "development",
        dev_tokens={
            TOKEN_A: {"sub": "cycle5-user-a", "scope": SCOPES_A, "agent_kind": "test"},
            TOKEN_B: {"sub": "cycle5-user-b", "scope": SCOPES_B, "agent_kind": "test"},
        },
    )
    app = create_app("xelibrary", settings)
    a = {"Authorization": f"Bearer {TOKEN_A}"}
    b = {"Authorization": f"Bearer {TOKEN_B}"}
    with TestClient(app) as client:
        table = client.post(
            "/api/v1/tables",
            headers=a,
            json={
                "path": "/private",
                "name": "user A private table",
                "columns": [
                    {"name": "title", "type": "string", "nullable": False, "default_value": None},
                ],
            },
        )
        assert table.status_code == 201, table.text
        table_id = table.json()["data"]["table_id"]
        row = client.post(
            f"/api/v1/tables/{table_id}/rows",
            headers=a,
            json={"values": {"title": "A_ONLY_CONFIDENTIAL_ROW"}},
        )
        assert row.status_code == 201, row.text
        row_id = row.json()["data"]["_row_id"]
        assert client.get(f"/api/v1/tables/{table_id}/rows/{row_id}", headers=a).status_code == 200
        acl = client.put(
            f"/api/v1/acl/table/{table_id}",
            headers={**a, "If-Match": '"revision:0"'},
            json={"grants": {"cycle5-user-b": []}},
        )
        assert acl.status_code == 200, acl.text
        listed = client.get("/api/v1/tables?limit=100", headers=b)
        assert listed.status_code == 200, listed.text
        assert all(item["id"] != table_id for item in listed.json()["data"]), listed.text
        denied = client.get(f"/api/v1/tables/{table_id}/rows/{row_id}", headers=b)
        assert denied.status_code in (403, 404), denied.text
        shared = client.post(
            "/api/v1/tables",
            headers=a,
            json={
                "path": "/bob-visible",
                "name": "B readable table",
                "columns": [
                    {"name": "title", "type": "string", "nullable": False, "default_value": None},
                ],
            },
        )
        assert shared.status_code == 201, shared.text
        shared_id = shared.json()["data"]["table_id"]
        shared_row = client.post(
            f"/api/v1/tables/{shared_id}/rows", headers=a, json={"values": {"title": "B_READABLE_ROW"}}
        )
        assert shared_row.status_code == 201, shared_row.text
        grant = client.put(
            f"/api/v1/acl/table/{shared_id}",
            headers={**a, "If-Match": '"revision:0"'},
            json={"grants": {"cycle5-user-b": ["read"]}},
        )
        assert grant.status_code == 200, grant.text
        theorem = client.post(
            "/api/v1/theorems",
            headers=a,
            json={
                "path": "/theorems/private",
                "title": "A_PRIVATE_THEOREM",
                "statement_natural": "Synthetic private statement",
                "assumptions": [],
                "evidence_refs": [],
            },
        )
        assert theorem.status_code == 201, theorem.text
        theorem_id = theorem.json()["data"]["theorem_id"]
    Path(args.manifest).write_text(
        json.dumps(
            {
                "table_id": table_id,
                "row_id": row_id,
                "shared_id": shared_id,
                "theorem_id": theorem_id,
                "token_a": TOKEN_A,
                "token_b": TOKEN_B,
            }
        )
    )
    print("LOCAL_XELIBRARY_READY", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
