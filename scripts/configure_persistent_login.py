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
    offline_role = client.get(realm + "/roles/offline_access", headers=headers)
    offline_role.raise_for_status()
    changed = 0
    first = 0
    while True:
        users = client.get(realm + "/users", headers=headers, params={"first": first, "max": 100})
        users.raise_for_status()
        if not users.json():
            break
        for user in users.json():
            if user.get("serviceAccountClientId") or user["username"].startswith("service-account-"):
                continue
            mappings = realm + "/users/" + user["id"] + "/role-mappings/realm"
            effective = client.get(mappings + "/composite", headers=headers)
            effective.raise_for_status()
            if not any(role["name"] == "offline_access" for role in effective.json()):
                client.post(mappings, headers=headers, json=[offline_role.json()]).raise_for_status()
                changed += 1
        first += len(users.json())
print(
    f"Desktop offline sessions enabled; offline role added to {changed} existing human accounts; passwords preserved"
)
