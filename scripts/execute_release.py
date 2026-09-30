"""Run an explicitly authorized, staged release via OIDC and Deploy Gateway."""

import json
import socket
import ssl
from pathlib import Path

import httpx
import jwt

# Operator workstation DNS adapter only; TLS hostname and certificate verification remain enabled.
original = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **kw: original(
    "192.168.0.101" if str(host).endswith("xenon.lan") else host, *a, **kw
)
root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(verify=context, trust_env=False, timeout=600) as client:

    def token(cid, key):
        result = client.post(
            "https://auth.xenon.lan/realms/xeonolab/protocol/openid-connect/token",
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": credentials[key]},
        )
        result.raise_for_status()
        return result.json()["access_token"]

    codex = token("codex-release", "release_secret")
    policy = token("deployment-policy", "approval_secret")
    claims = jwt.decode(
        codex,
        (root / "oidc-public.pem").read_text(),
        algorithms=["RS256"],
        issuer="https://auth.xenon.lan/realms/xeonolab",
        audience="xeonolab",
    )
    base = "https://deploy.xenon.lan"

    def api(path, body=None, access=codex):
        result = client.post(base + path, json=body, headers={"Authorization": "Bearer " + access})
        if not result.is_success:
            raise RuntimeError("Deploy API returned " + str(result.status_code) + " " + result.text)
        return result.json()["data"]

    release = api("/api/v1/releases", json.loads((root / "release.json").read_text()))
    rid = release["release_id"]
    api("/api/v1/releases/" + rid + "/approve", access=policy)
    lease = api(
        "/api/v1/deploy/lease/acquire", {"agent_id": claims["sub"], "release_id": rid, "ttl_seconds": 300}
    )
    print("Release approved and exclusive Codex lease acquired", flush=True)
    deployment = api(
        "/api/v1/deployments", {"release_id": rid, "lease_id": lease["lease_id"], "epoch": lease["epoch"]}
    )
    record = {
        "release_id": rid,
        "lease": lease,
        "deployment": deployment,
        "commit": json.loads((root / "release.json").read_text())["commit"],
    }
    Path("runtime/deployment-result.json").write_text(json.dumps(record, indent=2) + "\n")
    if deployment["state"] != "completed":
        raise RuntimeError("Deployment did not complete: " + deployment["state"])
    api("/api/v1/deploy/lease/" + lease["lease_id"] + "/release")
    print(json.dumps({"release_id": rid, **deployment, "lease_released": True}), flush=True)
