"""Loopback-only real XeLibrary API with temporary synthetic int64 records."""

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app

TOKEN = "issue216-synthetic-token"
SCOPES = "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach xelibrary:admin"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    settings = Settings(
        Path(args.data_dir),
        "development",
        dev_tokens={TOKEN: {"sub": "issue216-fixture-owner", "scope": SCOPES, "agent_kind": "test"}},
    )
    app = create_app("xelibrary", settings)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    definitions = [
        ("title", "string", False, None),
        ("external_id", "int64", False, None),
        ("minimum", "int64", False, -(2**63)),
        ("maximum", "int64", False, 2**63 - 1),
        ("negative", "int64", True, -9007199254740993),
        ("safe", "int64", False, 123),
        ("amount", "float64", False, 1.25),
        ("metadata", "json", True, {"number": 2**63 - 1, "text": "9007199254740993", "exponent": 1250.0, "__proto__": {"number": -(2**63)}}),
        ("optional", "int64", True, None),
    ]
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tables",
            headers=headers,
            json={"path": "/precision", "name": "int64 acceptance", "columns": [
                {"name": name, "type": kind, "nullable": nullable, "default_value": default}
                for name, kind, nullable, default in definitions
            ]},
        )
        assert created.status_code == 201, created.text
        table_id = created.json()["data"]["table_id"]
        created_row = client.post(
            f"/api/v1/tables/{table_id}/rows",
            headers=headers,
            json={"values": {"title": "original", "external_id": 9007199254740993}},
        )
        assert created_row.status_code == 201, created_row.text
        row_id = created_row.json()["data"]["_row_id"]
        row = client.get(f"/api/v1/tables/{table_id}/rows/{row_id}", headers=headers).json()["data"]
        assert row["values"]["external_id"] == 9007199254740993
        assert row["values"]["minimum"] == -(2**63)
        assert row["values"]["maximum"] == 2**63 - 1
    Path(args.manifest).write_text(json.dumps({"table_id": table_id, "row_id": row_id, "token": TOKEN}))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
