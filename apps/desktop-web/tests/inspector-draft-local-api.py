"""Loopback-only real API with temporary rows for Inspector draft acceptance."""

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app

TOKEN = "issue245-synthetic-token"
SCOPES = "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach xelibrary:admin"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    app = create_app("xelibrary", Settings(
        Path(args.data_dir), "development",
        dev_tokens={TOKEN: {"sub": "issue245-fixture-owner", "scope": SCOPES, "agent_kind": "test"}},
    ))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    definitions = [
        ("title", "string", False, None),
        ("notes", "text", True, "original notes"),
        ("external_id", "int64", False, 9007199254740993),
        ("payload", "json", True, {"number": 9223372036854775807}),
        ("flag", "boolean", True, None),
        ("ratio", "float64", True, None),
        ("optional", "string", True, None),
        ("day", "date", True, "2026-10-03"),
        ("moment", "datetime", True, "2026-10-03T00:00:00Z"),
        ("attachments", "attachment_list", True, []),
        ("status", "enum", True, "original"),
        ("related", "reference", True, "original-reference"),
        ("theorem", "theorem_ref", True, "original-theorem"),
    ]
    with TestClient(app) as client:
        response = client.post("/api/v1/tables", headers=headers, json={
            "path": "/draft-acceptance", "name": "Inspector draft acceptance",
            "columns": [{"name": name, "type": kind, "nullable": nullable, "default_value": default}
                        for name, kind, nullable, default in definitions],
        })
        assert response.status_code == 201, response.text
        table_id = response.json()["data"]["table_id"]
        row_ids = []
        for title in ("Source A", "Target B", "Target C"):
            response = client.post(f"/api/v1/tables/{table_id}/rows", headers=headers, json={"values": {"title": title}})
            assert response.status_code == 201, response.text
            row_ids.append(response.json()["data"]["_row_id"])
    Path(args.manifest).write_text(json.dumps({"table_id": table_id, "row_ids": row_ids, "token": TOKEN}))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
