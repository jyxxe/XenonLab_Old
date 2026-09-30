"""Seed real Forgejo tasks and verify independent OAuth claim fencing in production."""

import json
import ssl
from pathlib import Path

import httpx
import jwt

root = Path("runtime/deploy-secrets")
harness = json.loads((root / "harness-clients.json").read_text())
users = json.loads((root / "forgejo-users.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(verify=context, trust_env=False, timeout=60) as client:
    headers = []
    subjects = []
    for name, secret in harness.items():
        result = client.post(
            "https://192.168.0.101:9444/realms/xeonolab/protocol/openid-connect/token",
            data={"grant_type": "client_credentials", "client_id": name, "client_secret": secret},
        )
        result.raise_for_status()
        token = result.json()["access_token"]
        claims = jwt.decode(
            token,
            (root / "oidc-public.pem").read_text(),
            algorithms=["RS256"],
            issuer="https://192.168.0.101:9444/realms/xeonolab",
            audience="xeonolab",
        )
        assert not any(scope.startswith("deploy:") for scope in claims.get("platform_scopes", "").split())
        subjects.append(claims["sub"])
        headers.append({"Authorization": "Bearer " + token})
    assert subjects[0] != subjects[1]
    issues = client.get(
        "https://192.168.0.101:9445/api/v1/repos/xenon-admin/XeonoLab/issues",
        params={"limit": 100, "state": "open"},
        auth=("xenon-admin", users["xenon-admin"]),
    )
    issues.raise_for_status()
    base = "https://192.168.0.101:9443/api/v1"
    assert client.get(base + "/work").status_code == 401
    items = []
    for issue in issues.json():
        if not issue["title"].startswith("[Plan "):
            continue
        result = client.post(
            base + "/work",
            headers=headers[0],
            json={"repository": "xenon-admin/XeonoLab", "issue_id": issue["number"], "title": issue["title"]},
        )
        result.raise_for_status()
        items.append(result.json()["data"])
    assert len(items) == 30
    item = next(item for item in items if item["status"] == "queued")
    path = base + "/work/" + item["id"]
    result = client.post(path + "/claim", headers=headers[0], json={"ttl_seconds": 30})
    result.raise_for_status()
    epoch = result.json()["data"]["epoch"]
    try:
        assert client.post(path + "/claim", headers=headers[1], json={}).status_code == 409
        assert client.post(path + "/release", headers=headers[1], json={"epoch": epoch}).status_code == 409
    finally:
        client.post(path + "/release", headers=headers[0], json={"epoch": epoch}).raise_for_status()
    assert client.get(base + "/work", headers=headers[1]).status_code == 200
print(
    "PASS: 30 real Issues queued; independent JWT identities; exclusive claim and holder fencing; no deploy scopes"
)
