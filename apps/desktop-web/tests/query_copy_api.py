"""Ephemeral localhost XeLibrary with explicitly synthetic data and identity."""
import json
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app


def main():
    with TemporaryDirectory(prefix="xeonolab-query-copy-") as data_dir:
        scopes = "xelibrary:read xelibrary:write xelibrary:query"
        settings = Settings(
            Path(data_dir), "development",
            dev_tokens={"query-copy-synthetic": {"sub": "query-copy-synthetic-owner", "scope": scopes}},
        )
        app = create_app("xelibrary", settings)
        calls = []

        @app.middleware("http")
        async def log_requests(request, call_next):
            if request.url.path.startswith("/api/v1/"):
                calls.append({"method": request.method, "path": request.url.path})
            return await call_next(request)

        client = TestClient(app)
        headers = {"Authorization": "Bearer query-copy-synthetic"}
        tables = []
        for index in range(2):
            columns = [
                {"name": "status", "type": "string"},
                {"name": "amount", "type": "int64"},
                {"name": "integer", "type": "int64"},
                {"name": "payload", "type": "json"},
            ]
            if index:
                columns.append({"name": "secondary", "type": "string"})
            result = client.post("/api/v1/tables", headers=headers, json={
                "path": f"/S215/{index}", "name": ['=合成表 A\t"引号"\r\n第二行', "\t合成表 B"][index], "columns": columns,
            })
            assert result.status_code == 201, result.text
            tables.append(result.json()["data"]["table_id"])
        for index, (table, status, amount) in enumerate([(0, "ready", 30), (1, "ready", 20), (0, "ready", 10), (1, "draft", 40)]):
            exact_integer = [(1 << 63) - 1, -(1 << 63), 9007199254740993, 9007199254740991][index]
            values = {"status": status, "amount": amount, "integer": exact_integer, "payload": {
                "null": None, "tab": "a\tb", "CRLF": "a\r\nb", "quote": 'say "hello"',
                "nested": {"中文": [0, False, None, {"index": index}]}, "long": "x" * 220,
                "endpoints": {"max": (1 << 63) - 1, "min": -(1 << 63)},
                "endpoint_array": [(1 << 63) - 1, {"min": -(1 << 63), "numericString": "9223372036854775807"}],
            }}
            if table:
                values["secondary"] = "different schema"
            result = client.post(f"/api/v1/tables/{tables[table]}/rows", headers=headers, json={"values": values})
            assert result.status_code == 201, result.text
        expression = 'FROM "/S215/**" WHERE status = "ready" ORDER BY amount DESC LIMIT 10'
        expected = client.post("/api/v1/query", headers=headers, json={"expression": expression}).json()["data"]
        assert len(expected) == 3
        # Strings preserve original decimal tokens across the browser's ordinary
        # fixture metadata parse; actual Query responses use parseAPIJSON.
        expected_values_json = [json.dumps(row["values"], ensure_ascii=False, separators=(",", ":")) for row in expected]
        names = {row["id"]: row["name"] for row in client.get("/api/v1/tables", headers=headers).json()["data"]}
        calls.clear()

        @app.get("/api/v1/users/me")
        def synthetic_identity():
            # The real query/table routes still authenticate the development token.
            return {"data": {"subject": "query-copy-synthetic-owner", "scopes": scopes.split()}, "meta": {}}

        @app.get("/__fixture")
        def fixture():
            return {"synthetic": True, "rows": expected, "expected_values_json": expected_values_json,
                    "names": names, "expected_names": {key: "'" + value for key, value in names.items()},
                    "expression": expression, "calls": calls}

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            print(json.dumps({"port": sock.getsockname()[1]}), flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])


if __name__ == "__main__":
    main()
