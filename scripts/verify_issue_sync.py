"""Exercise actual Forgejo signed deliveries without injecting synthetic HTTP events."""

import json
import ssl
import time
from pathlib import Path
from uuid import uuid4

import httpx

root = Path("runtime/deploy-secrets")
users = json.loads((root / "forgejo-users.json").read_text())
harness = json.loads((root / "harness-clients.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(verify=context, trust_env=False, timeout=60) as client:
    token = client.post(
        "https://192.168.0.101:9444/realms/xeonolab/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "codex-dev-01",
            "client_secret": harness["codex-dev-01"],
        },
    )
    token.raise_for_status()
    headers = {"Authorization": "Bearer " + token.json()["access_token"]}
    base = "https://192.168.0.101:9445/api/v1/repos/xenon-admin/XeonoLab/issues"
    auth = ("xenon-admin", users["xenon-admin"])
    issue = client.post(
        base,
        auth=auth,
        json={
            "title": "[Smoke] signed sync " + str(uuid4()),
            "body": "Temporary integration check. This Issue is closed after verification.",
        },
    )
    issue.raise_for_status()
    number = issue.json()["number"]

    def wait(state):
        for _ in range(40):
            result = client.get("https://192.168.0.101:9443/api/v1/work?limit=1000", headers=headers)
            result.raise_for_status()
            item = next((item for item in result.json()["data"] if item["issue_id"] == number), None)
            if item and item["status"] == state:
                return item
            time.sleep(1)
        raise RuntimeError("Real webhook did not synchronize " + state)

    try:
        item = wait("queued")
        path = "https://192.168.0.101:9443/api/v1/work/" + item["id"]
        claimed = client.post(path + "/claim", headers=headers, json={"ttl_seconds": 30})
        claimed.raise_for_status()
        epoch = claimed.json()["data"]["epoch"]
        time.sleep(2)
        client.patch(base + "/" + str(number), auth=auth, json={"state": "closed"}).raise_for_status()
        wait("completed")
        assert client.post(path + "/release", headers=headers, json={"epoch": epoch}).status_code == 409
        time.sleep(2)
        client.patch(base + "/" + str(number), auth=auth, json={"state": "open"}).raise_for_status()
        wait("queued")
    finally:
        time.sleep(2)
        client.patch(base + "/" + str(number), auth=auth, json={"state": "closed"}).raise_for_status()
    wait("completed")
print("PASS: real signed Issue open → queue; close → fenced claim; reopen → queue; smoke Issue closed")
