"""Read-only production validation of permission console APIs."""

import json
import ssl
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(verify=context, trust_env=False, timeout=60) as client:
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
    base = "https://192.168.0.101:9443"
    for path in ("/api/v1/users/me", "/api/v1/acl/path/%2F", "/api/v1/audit"):
        assert client.get(base + path).status_code == 401
        response = client.get(base + path, headers=headers)
        response.raise_for_status()
        assert "data" in response.json()
    print("PASS: production identity / ACL inspection / audit; anonymous access denied")
