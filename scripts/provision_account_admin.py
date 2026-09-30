"""Provision a server-only realm user manager, never a master admin credential."""

import json
import secrets
import ssl
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
secret_path = root / "account-admin-secret"
if not secret_path.exists():
    secret_path.touch(mode=0o600)
    secret_path.write_text(secrets.token_urlsafe(48))
secret_path.chmod(0o600)
credentials = json.loads((root / "credentials.json").read_text())
base = "https://192.168.0.101:9444"
with httpx.Client(
    verify=ssl.create_default_context(cafile=root / "lan-root-ca.crt"), trust_env=False, timeout=30
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
    body = {
        "clientId": "desktop-account-admin",
        "enabled": True,
        "publicClient": False,
        "secret": secret_path.read_text(),
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "protocol": "openid-connect",
    }
    existing = client.get(realm + "/clients", headers=headers, params={"clientId": body["clientId"]})
    existing.raise_for_status()
    if existing.json():
        cid = existing.json()[0]["id"]
        client.put(realm + "/clients/" + cid, headers=headers, json=body).raise_for_status()
    else:
        created = client.post(realm + "/clients", headers=headers, json=body)
        created.raise_for_status()
        cid = created.headers["Location"].rsplit("/", 1)[1]
    user = client.get(realm + "/clients/" + cid + "/service-account-user", headers=headers)
    user.raise_for_status()
    management = client.get(realm + "/clients", headers=headers, params={"clientId": "realm-management"})
    management.raise_for_status()
    mid = management.json()[0]["id"]
    roles = []
    for name in ("manage-users", "view-users", "query-users", "view-realm"):
        role = client.get(realm + "/clients/" + mid + "/roles/" + name, headers=headers)
        role.raise_for_status()
        roles.append(role.json())
    client.post(
        realm + "/users/" + user.json()["id"] + "/role-mappings/clients/" + mid, headers=headers, json=roles
    ).raise_for_status()

    desktop = client.get(realm + "/clients", headers=headers, params={"clientId": "desktop"})
    desktop.raise_for_status()
    did = desktop.json()[0]["id"]
    mapper_url = realm + "/clients/" + did + "/protocol-mappers/models"
    mappers = client.get(mapper_url, headers=headers)
    mappers.raise_for_status()
    mapper = {
        "name": "account-credential-epoch",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-usermodel-attribute-mapper",
        "config": {
            "user.attribute": "xeonolab_credential_epoch",
            "claim.name": "account_epoch",
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }
    old = next((m for m in mappers.json() if m["name"] == mapper["name"]), None)
    if old:
        client.put(mapper_url + "/" + old["id"], headers=headers, json=mapper).raise_for_status()
    else:
        client.post(mapper_url, headers=headers, json=mapper).raise_for_status()
    profile = client.get(realm + "/users/profile", headers=headers)
    profile.raise_for_status()
    body = profile.json()
    attributes = body.setdefault("attributes", [])
    definition = {
        "name": "xeonolab_credential_epoch",
        "displayName": "Credential version",
        "permissions": {"view": ["admin"], "edit": ["admin"]},
        "multivalued": False,
    }
    attributes[:] = [a for a in attributes if a["name"] != definition["name"]] + [definition]
    client.put(realm + "/users/profile", headers=headers, json=body).raise_for_status()
print("Desktop credential version mapper and admin-only attribute configured")
