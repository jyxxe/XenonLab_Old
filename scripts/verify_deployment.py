"""Production smoke through verified LAN TLS, using an operator policy account."""

import json
import socket
import ssl
import time
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
original = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **kw: original(
    "192.168.0.101" if str(host).endswith("xenon.lan") else host, *a, **kw
)
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(verify=context, trust_env=False, timeout=180) as client:
    result = client.post(
        "https://192.168.0.101:9444/realms/xeonolab/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "deployment-policy",
            "client_secret": credentials["approval_secret"],
        },
    )
    result.raise_for_status()
    headers = {"Authorization": "Bearer " + result.json()["access_token"]}
    for host in ["xenon.lan", "library.xenon.lan", "tools.xenon.lan", "deploy.xenon.lan", "llm.xenon.lan"]:
        result = client.get("https://" + host + "/health/ready")
        assert result.status_code == 200, (host, result.text)
    assert client.get("https://xenon.lan/api/v1/tables").status_code == 401
    base = "https://xenon.lan/api/v1"

    def api(method, path, body=None, extra=None):
        result = client.request(method, base + path, json=body, headers={**headers, **(extra or {})})
        assert result.is_success, (path, result.status_code, result.text)
        return result

    tables = api("GET", "/tables").json()["data"]
    existing = next(
        (t for t in tables if t["path"] in ("/示例", "/示例/平台") and t["name"] == "部署验收"), None
    )
    if existing:
        tid = existing["id"]
    else:
        tid = api(
            "POST",
            "/tables",
            {
                "path": "/示例/平台",
                "name": "部署验收",
                "columns": [
                    {"name": "title", "type": "string", "nullable": False},
                    {"name": "body", "type": "text", "nullable": False},
                    {"name": "language", "type": "string", "default_value": "zh"},
                ],
            },
        ).json()["data"]["table_id"]
    table = api("GET", f"/tables/{tid}")
    if table.json()["data"]["path"] == "/示例":
        api("POST", f"/tables/{tid}/move", {"path": "/示例/平台"}, {"If-Match": table.headers["ETag"]})
    rows = [
        ("一致收敛与积分交换", "在有限测度空间上，一致收敛允许在适当条件下交换极限与积分。"),
        (
            "Dominated convergence",
            "An integrable dominating function permits passing the limit through an integral under dominated convergence.",
        ),
    ]
    for number, (title, body) in enumerate(rows):
        api(
            "POST",
            f"/tables/{tid}/rows",
            {"values": {"title": title, "body": body}},
            {"Idempotency-Key": f"production-sample-{number}"},
        )
    query = api("POST", "/query", {"expression": 'FROM "/示例/**" LIMIT 20'}).json()
    assert len(query["data"]) >= 2
    job = api("POST", "/index/reindex", {"table_id": tid}).json()["data"]["job_id"]
    for attempt in range(60):
        status = api("GET", "/jobs/" + job).json()["data"]["status"]
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError("production index failed")
        time.sleep(0.5)
    else:
        raise RuntimeError("index timeout")
    found = api("POST", "/search/semantic", {"query": "一致收敛与积分交换", "path": "/示例/**"}).json()
    assert found["data"] and found["meta"]["partial"] is False, found
    assert found["data"][0]["score_type"] == "d100m_cosine"
    tool = client.post(
        "https://tools.xenon.lan/api/v1/tools/xelibrary.query",
        headers=headers,
        json={"expression": 'FROM "/示例/**" LIMIT 20'},
    )
    assert tool.status_code == 200 and len(tool.json()["data"]) >= 2, tool.text
    catalogue = api("GET", "/apps").json()["data"]
    if not any(a["id"] == "xelibrary" for a in catalogue):
        api(
            "POST",
            "/apps",
            {
                "id": "xelibrary",
                "title": "XeLibrary",
                "origin": "https://library.xenon.lan",
                "icon": "◇",
                "permissions": ["xelibrary:read"],
                "default_size": [1100, 740],
                "singleton": True,
            },
        )
    assert "XeonoLab" in client.get("https://xenon.lan/").text
    report = {
        "https": "verified local CA",
        "services_ready": 5,
        "unauthenticated": "401",
        "table_id": tid,
        "sample_rows": len(query["data"]),
        "index_job": job,
        "semantic": "real d100m_cosine",
        "tool_user_context": "preserved",
        "desktop": "served",
    }
    Path("runtime/production-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False))
