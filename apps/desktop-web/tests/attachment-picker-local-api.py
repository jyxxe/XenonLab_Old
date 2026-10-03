"""Loopback-only real XeLibrary fixture; synthetic tokens and temporary data."""
import argparse
import json
import sqlite3
from pathlib import Path
from uuid import UUID

import uvicorn
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    token = "attachment-picker-local-synthetic-owner"
    scopes = "xelibrary:read xelibrary:write xelibrary:attach xelibrary:admin"
    settings = Settings(Path(args.data_dir), "development", rate_limit_enabled=False, dev_tokens={
        token: {"sub": "synthetic-owner", "scope": scopes, "agent_kind": "test"},
        "picker-reader": {"sub": "synthetic-reader", "scope": "xelibrary:read", "agent_kind": "test"},
        "picker-attach": {"sub": "synthetic-reader", "scope": "xelibrary:read xelibrary:attach", "agent_kind": "test"},
        "picker-outsider": {"sub": "synthetic-outsider", "scope": "xelibrary:read xelibrary:attach", "agent_kind": "test"},
    })
    app = create_app("xelibrary", settings)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        table = client.post("/api/v1/tables", headers=headers, json={"path": "/Synthetic", "name": "Picker table",
            "columns": [{"name": "title", "type": "string"}]}).json()["data"]["table_id"]
        rows = {}
        for name in ("source", "source_second", "hidden", "target", "denied_target"):
            rows[name] = client.post(f"/api/v1/tables/{table}/rows", headers=headers,
                                    json={"values": {"title": name.replace("_", " ")}}).json()["data"]["_row_id"]
        uploaded = client.post("/api/v1/attachments", headers=headers,
                               params={"table_id": table, "row_id": rows["source"]},
                               files={"upload": ("proof.pdf", b"%PDF-local-picker-synthetic")}).json()["data"]
        with sqlite3.connect(settings.data_dir / "tables" / f"{table}.xl") as db:
            db.execute("DELETE FROM __xl_attachments")
            for i in range(1, 28):
                db.execute("INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)", (
                    str(UUID(int=i)), rows["source"] if i <= 26 else rows["source_second"],
                    uploaded["object_hash"], f"proof-{i:02}.pdf", "application/pdf", uploaded["size"],
                    "2026-01-01T00:00:00Z", "{}"))
            db.execute("INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)", (
                str(UUID(int=28)), rows["hidden"], uploaded["object_hash"], "hidden-private.pdf",
                "application/pdf", uploaded["size"], "2026-01-01T00:00:00Z", "{}"))
        def acl(kind, resource, grants):
            url=f"/api/v1/acl/{kind}/{resource}"
            current=client.get(url,headers=headers)
            result=client.put(url,headers=headers|{"If-Match":current.headers["ETag"]},json={"grants":grants})
            assert result.status_code == 200, result.text
        acl("table",table,{"synthetic-owner":["read","attach"],"synthetic-reader":["read","attach"]})
        acl("row",f"{table}:{rows['hidden']}",{"synthetic-owner":["read","attach"]})
        acl("row",f"{table}:{rows['denied_target']}",{"synthetic-owner":["read","attach"],"synthetic-reader":["read"]})
    Path(args.manifest).write_text(json.dumps({"table_id":table,**rows,"token":"picker-attach",
        "source_attachment":str(UUID(int=1)),"hidden_attachment":str(UUID(int=28)),"owner_token":token}))
    uvicorn.run(app,host="127.0.0.1",port=args.port,log_level="warning")


if __name__ == "__main__":
    main()
