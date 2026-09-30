"""Enable desktop offline sessions without changing existing users or credentials."""

import json
import ssl
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
base = "https://192.168.0.101:9444"
with httpx.Client(
    verify=ssl.create_default_context(cafile=root / "lan-root-ca.crt"), trust_env=False, timeout=60
) as client:
    result = client.post(
        base + "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "bootstrap-admin",
            "password": credentials["keycloak_admin"],
        },
    )
    result.raise_for_status()
    headers = {"Authorization": "Bearer " + result.json()["access_token"]}
    realm = base + "/admin/realms/xeonolab"
    client.put(
        realm,
        headers=headers,
        json={
            "offlineSessionIdleTimeout": 2147483647,
            "offlineSessionMaxLifespanEnabled": False,
        },
    ).raise_for_status()
    desktop = client.get(realm + "/clients", headers=headers, params={"clientId": "desktop"})
    desktop.raise_for_status()
    cid = desktop.json()[0]["id"]
    scopes = client.get(realm + "/client-scopes", headers=headers)
    scopes.raise_for_status()
    offline = next(scope for scope in scopes.json() if scope["name"] == "offline_access")
    client.put(
        realm + "/clients/" + cid + "/optional-client-scopes/" + offline["id"], headers=headers
    ).raise_for_status()
print("Desktop offline sessions enabled; existing users and passwords preserved")
