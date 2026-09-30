"""Provision independent development-only OAuth clients; no deploy scopes."""

import json
import secrets
import ssl
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
path = root / "harness-clients.json"
default_clients = {
    "codex-dev-01": "codex",
    "opencode-01": "opencode",
    "chatgpt-web-dev-01": "agent",
    "zcode-dev-01": "agent",
    "general-agent-dev-01": "agent",
}
clients = json.loads(path.read_text()) if path.exists() else {}
changed = False
for name in default_clients:
    if name not in clients:
        clients[name] = secrets.token_urlsafe(40)
        changed = True
if changed or not path.exists():
    path.write_text(json.dumps(clients, sort_keys=True) + "\n")
    path.chmod(0o600)
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(verify=context, trust_env=False, timeout=60) as client:
    response = client.post(
        "https://192.168.0.101:9444/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "bootstrap-admin",
            "password": credentials["keycloak_admin"],
        },
    )
    response.raise_for_status()
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    base = "https://192.168.0.101:9444/admin/realms/xeonolab/clients"
    for name, secret in clients.items():
        config = {
            "clientId": name,
            "enabled": True,
            "publicClient": False,
            "secret": secret,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "protocol": "openid-connect",
            "protocolMappers": [
                {
                    "name": "platform_scopes",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-hardcoded-claim-mapper",
                    "config": {
                        "claim.name": "platform_scopes",
                        "claim.value": "dev:read dev:write desktop:read",
                        "jsonType.label": "String",
                        "access.token.claim": "true",
                    },
                },
                {
                    "name": "agent_kind",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-hardcoded-claim-mapper",
                    "config": {
                        "claim.name": "agent_kind",
                        "claim.value": default_clients.get(
                            name,
                            "codex" if name.startswith("codex") else "opencode" if name.startswith("opencode") else "agent",
                        ),
                        "jsonType.label": "String",
                        "access.token.claim": "true",
                    },
                },
                {
                    "name": "audience",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-audience-mapper",
                    "config": {"included.custom.audience": "xeonolab", "access.token.claim": "true"},
                },
            ],
        }
        old = client.get(base, params={"clientId": name}, headers=headers)
        old.raise_for_status()
        if old.json():
            client.put(base + "/" + old.json()[0]["id"], json=config, headers=headers).raise_for_status()
        else:
            client.post(base, json=config, headers=headers).raise_for_status()
print("Independent development OAuth clients provisioned without production deploy scopes")
