"""Generate bootstrap realm and local-only credentials. Output paths, never secrets."""

import json
import os
import secrets
from pathlib import Path

root = Path("runtime/deploy-secrets")
root.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(root, 0o700)
credential_file = root / "credentials.json"
if credential_file.exists():
    credentials = json.loads(credential_file.read_text())
else:
    credentials = {
        name: secrets.token_urlsafe(32)
        for name in [
            "keycloak_admin",
            "postgres",
            "admin_password",
            "embedding_secret",
            "release_secret",
            "approval_secret",
        ]
    }
    credential_file.write_text(json.dumps(credentials, indent=2) + "\n")
    os.chmod(credential_file, 0o600)


def hardcoded(name, value):
    return {
        "name": name,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-hardcoded-claim-mapper",
        "config": {
            "claim.name": name,
            "claim.value": value,
            "jsonType.label": "String",
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }


def audience():
    return {
        "name": "xeonolab-audience",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.custom.audience": "xeonolab",
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }


def client(cid, scope, kind, secret):
    return {
        "clientId": cid,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "secret": secret,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "protocolMappers": [audience(), hardcoded("platform_scopes", scope), hardcoded("agent_kind", kind)],
    }


admin_scopes = "desktop:read desktop:admin xelibrary:read xelibrary:write xelibrary:query xelibrary:attach xelibrary:admin deploy:read llm:use"
realm = {
    "realm": "xeonolab",
    "enabled": True,
    "sslRequired": "external",
    "registrationAllowed": False,
    "accessTokenLifespan": 300,
    "ssoSessionIdleTimeout": 1800,
    "ssoSessionMaxLifespan": 28800,
    "roles": {"realm": [{"name": "platform-admin"}]},
    "users": [
        {
            "username": "admin",
            "enabled": True,
            "emailVerified": True,
            "firstName": "Platform",
            "lastName": "Administrator",
            "realmRoles": ["platform-admin"],
            "credentials": [{"type": "password", "value": credentials["admin_password"], "temporary": True}],
        }
    ],
    "clients": [
        {
            "clientId": "desktop",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": ["https://xenon.lan/*", "https://192.168.0.101:9443/*"],
            "webOrigins": ["https://xenon.lan", "https://192.168.0.101:9443"],
            "attributes": {"pkce.code.challenge.method": "S256"},
            "protocolMappers": [
                audience(),
                {
                    "name": "admin-scope",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-usermodel-realm-role-mapper",
                    "config": {
                        "claim.name": "roles",
                        "jsonType.label": "String",
                        "multivalued": "true",
                        "access.token.claim": "true",
                    },
                },
            ],
        },
        client(
            "embedding-indexer", "embedding:read embedding:write", "service", credentials["embedding_secret"]
        ),
        client(
            "codex-release",
            "deploy:read deploy:release deploy:execute",
            "codex",
            credentials["release_secret"],
        ),
        client(
            "deployment-policy",
            "deploy:approve deploy:read desktop:read desktop:admin xelibrary:admin xelibrary:read xelibrary:write xelibrary:query",
            "policy",
            credentials["approval_secret"],
        ),
    ],
}
(root / "xeonolab-realm.json").write_text(json.dumps(realm, indent=2))
os.chmod(root / "xeonolab-realm.json", 0o600)
(root / "keycloak.env").write_text(
    "\n".join(
        [
            "KC_DB=postgres",
            "KC_DB_URL=jdbc:postgresql://127.0.0.1:5432/keycloak",
            "KC_DB_USERNAME=keycloak",
            "KC_DB_PASSWORD=" + credentials["postgres"],
            "KC_BOOTSTRAP_ADMIN_USERNAME=bootstrap-admin",
            "KC_BOOTSTRAP_ADMIN_PASSWORD=" + credentials["keycloak_admin"],
            "KC_HOSTNAME=https://auth.xenon.lan",
            "KC_HTTP_ENABLED=true",
            "KC_PROXY_HEADERS=xforwarded",
            "KC_HEALTH_ENABLED=true",
            "JAVA_OPTS_APPEND=-Xms256m -Xmx1536m",
        ]
    )
    + "\n"
)
os.chmod(root / "keycloak.env", 0o600)
(root / "postgres.env").write_text(
    "POSTGRES_DB=keycloak\nPOSTGRES_USER=keycloak\nPOSTGRES_PASSWORD=" + credentials["postgres"] + "\n"
)
os.chmod(root / "postgres.env", 0o600)
print("OIDC configuration and credentials prepared under runtime/deploy-secrets (mode 600)")
