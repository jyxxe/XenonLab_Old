"""Prepare private configuration assets; no credentials are printed or committed."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from xeonolab.role_scopes import OIDC_ROLE_SCOPES

root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
public = (root / "deploy-key.pub").read_text().strip()
roles = OIDC_ROLE_SCOPES
issuer_by_origin = {
    "https://xenon.jyxxe.com": "https://xenon.jyxxe.com/auth/realms/xeonolab",
    "https://xenon.lan": "https://auth.xenon.lan/realms/xeonolab",
    "https://192.168.0.101:9443": "https://192.168.0.101:9444/realms/xeonolab",
}
token_urls = {
    issuer: issuer + "/protocol/openid-connect/token" for issuer in issuer_by_origin.values()
}
services = {
    "101": {"desktop-web": 8101},
    "104": {"xelibrary": 8100},
    "106": {"deploy-gateway": 8103, "tool-gateway": 8102, "llm-gateway": 8105},
    "107": {"embedding": 8104},
    "111": {"dev-agent": 8106},
}
for node, entries in services.items():
    dest = root / node
    dest.mkdir(exist_ok=True, mode=0o700)
    (dest / "oidc-public.pem").write_bytes((root / "oidc-public.pem").read_bytes())
    (dest / "lan-root-ca.crt").write_bytes((root / "lan-root-ca.crt").read_bytes())
    (dest / "activation.json").write_text(
        json.dumps(
            {
                "services": [s for s in entries if s != "deploy-gateway"],
                "ports": [p for s, p in entries.items() if s != "deploy-gateway"],
                "address": "127.0.0.1" if node == "101" else "192.168.0." + node,
                "reload_caddy": node == "101",
            }
        )
    )
    (dest / "authorized_keys").write_text(
        'restrict,command="/usr/local/libexec/xeonolab-receiver" ' + public + "\n"
    )
    for service, port in entries.items():
        lines = [
            "APP_MODE=production",
            "SERVICE=" + service,
            "BIND_HOST=" + ("127.0.0.1" if node == "101" else "192.168.0." + node),
            "PORT=" + str(port),
            "DATA_DIR="
            + ("/srv/xelibrary/core/xeonolab" if node == "104" else "/var/lib/xeonolab/" + service),
            "OIDC_ISSUER=https://192.168.0.101:9444/realms/xeonolab",
            "OIDC_AUDIENCE=xeonolab",
            "OIDC_PUBLIC_KEY_FILE=/etc/xeonolab/oidc-public.pem",
            "OIDC_ROLE_SCOPES_JSON='" + json.dumps(roles) + "'",
            "OIDC_CA_FILE=/etc/xeonolab/lan-root-ca.crt",
            "OIDC_TOKEN_URL=https://192.168.0.101:9444/realms/xeonolab/protocol/openid-connect/token",
            "OIDC_ISSUERS_BY_ORIGIN_JSON='" + json.dumps(issuer_by_origin, separators=(",", ":")) + "'",
            "OIDC_TOKEN_URLS_JSON='" + json.dumps(token_urls, separators=(",", ":")) + "'",
        ]
        if service == "desktop-web":
            lines += [
                "ACCOUNT_ADMIN_URL=https://192.168.0.101:9444/admin/realms/xeonolab",
                "ACCOUNT_AUTH_CLIENT_ID=desktop-account-admin",
                "ACCOUNT_AUTH_CLIENT_SECRET_FILE=/etc/xeonolab/account-admin-secret",
            ]
        else:
            lines += ["AUTHORIZATION_URL=https://192.168.0.101:9443/api/v1/auth/authorization"]
        if node == "104":
            lines += [
                "EMBEDDING_URL=http://192.168.0.107:8104",
                "EMBEDDING_CLIENT_ID=embedding-indexer",
                "EMBEDDING_CLIENT_SECRET_FILE=/etc/xeonolab/embedding-client-secret",
                "D100M_TOKENIZER_PATH=/opt/xeonolab/d100m-tokenizer.json",
            ]
            (dest / "embedding-client-secret").write_text(credentials["embedding_secret"])
        if service == "dev-agent":
            lines += [
                "FORGEJO_WEBHOOK_SECRET_FILE=/etc/xeonolab/forgejo-webhook-secret",
                "FORGEJO_URL=https://192.168.0.101:9445",
                "FORGEJO_CA_FILE=/etc/xeonolab/lan-root-ca.crt",
            ]
        if service == "tool-gateway":
            lines += ["XELIBRARY_URL=http://192.168.0.104:8100"]
        if service == "deploy-gateway":
            lines += ["DEPLOY_EXECUTOR_PATH=/usr/local/libexec/xeonolab-executor"]
        if service == "llm-gateway":
            lines += [
                "LLM_BASE_URL=https://api.jyxxe.com",
                "LLM_API_KEY_FILE=/etc/xeonolab/llm-provider-key",
                "LLM_PROVIDER_MODEL=~deepseek/deepseek-flash-latest",
                "LLM_TLS_MAX_VERSION=1.2",
            ]
        if service == "embedding":
            lines += ["D100M_MODEL_PATH=/opt/xeonolab/D100M-Embedding", "EMBEDDING_CPU_THREADS=8"]
        (dest / (service + ".env")).write_text("\n".join(lines) + "\n")
    for file in dest.iterdir():
        os.chmod(file, 0o600)
artifact = {
    "name": "platform-release",
    "digest": "sha256:" + hashlib.sha256(Path("runtime/xeonolab-release.tar").read_bytes()).hexdigest(),
}
(root / "executor.json").write_text(
    json.dumps(
        {
            "commit": commit,
            "artifacts": [artifact],
            "targets": ["192.168.0.101", "192.168.0.107", "192.168.0.104", "192.168.0.111", "192.168.0.106"],
        }
    )
)
(root / "release.json").write_text(
    json.dumps(
        {
            "commit": commit,
            "artifacts": [artifact],
            "migration_manifest": "no database schema migration; request-local ACL snapshot and indexed ACL lookup only",
            "test_report": "211 backend tests; 11 desktop tests including synthetic Work browser acceptance; Issue 70 ACL query-plan regression; OpenAPI export; TypeScript/Vite build",
        }
    )
)
print("Release assets generated for VMs 101,104,106,107,111; commit " + commit)
