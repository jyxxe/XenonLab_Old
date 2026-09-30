"""Verify real browser-code PKCE semantics with a disposable realm user."""

import base64
import hashlib
import json
import secrets
import socket
import ssl
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.hidden = {}

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs.get("action")
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.hidden[attrs["name"]] = attrs.get("value", "")


ip_only = "--ip" in sys.argv
auth_base = "https://192.168.0.101:9444"  # Canonical issuer is independent of the desktop origin.
desktop_url = "https://192.168.0.101:9443" if ip_only else "https://xenon.jyxxe.com"

original = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **kw: original(
    "192.168.0.101" if str(host).endswith("xenon.lan") else host, *a, **kw
)
root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
context.load_default_certs()
with httpx.Client(
    verify=context,
    trust_env=False,
    timeout=30,
    proxy="http://127.0.0.1:7897" if "--proxy" in sys.argv else None,
) as client:
    if ip_only:
        config = client.get(desktop_url + "/api/v1/auth/config").json()
        assert config["issuer"] == auth_base + "/realms/xeonolab"
        discovery = client.get(config["issuer"] + "/.well-known/openid-configuration").json()
        assert discovery["issuer"] == config["issuer"]
        assert urlparse(discovery["authorization_endpoint"]).hostname == urlparse(auth_base).hostname
    r = client.post(
        auth_base + "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "bootstrap-admin",
            "password": credentials["keycloak_admin"],
        },
    )
    r.raise_for_status()
    headers = {"Authorization": "Bearer " + r.json()["access_token"]}
    base = auth_base + "/admin/realms/xeonolab"
    username = "deployment-smoke-" + str(uuid4())
    password = secrets.token_urlsafe(32)
    r = client.post(
        base + "/users",
        headers=headers,
        json={
            "username": username,
            "email": username + "@xenon.lan",
            "firstName": "Smoke",
            "lastName": "Test",
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        },
    )
    r.raise_for_status()
    uid = r.headers["Location"].rsplit("/", 1)[1]
    try:
        if "--legacy-user" in sys.argv:
            mappings = base + "/users/" + uid + "/role-mappings/realm"
            existing = client.get(mappings, headers=headers)
            existing.raise_for_status()
            client.request("DELETE", mappings, headers=headers, json=existing.json()).raise_for_status()
            effective = client.get(mappings + "/composite", headers=headers)
            effective.raise_for_status()
            assert not any(role["name"] == "offline_access" for role in effective.json())
            subprocess.run([sys.executable, "scripts/configure_persistent_login.py"], check=True)
            effective = client.get(mappings + "/composite", headers=headers)
            effective.raise_for_status()
            assert any(role["name"] == "offline_access" for role in effective.json())
        role = client.get(base + "/roles/platform-admin", headers=headers)
        role.raise_for_status()
        client.post(
            base + "/users/" + uid + "/role-mappings/realm", headers=headers, json=[role.json()]
        ).raise_for_status()
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(24)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        redirect = desktop_url + "/"
        r = client.get(
            auth_base + "/realms/xeonolab/protocol/openid-connect/auth",
            params={
                "client_id": "desktop",
                "response_type": "code",
                "redirect_uri": redirect,
                "scope": "openid profile offline_access" if "--session" in sys.argv else "openid profile",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            },
        )
        assert r.status_code == 200
        form = LoginForm()
        form.feed(r.text)
        assert form.action, "login form missing"
        if ip_only:
            assert urlparse(form.action).hostname == urlparse(auth_base).hostname
        r = client.post(form.action, data={**form.hidden, "username": username, "password": password})
        assert r.status_code in (302, 303), ("login did not finish", r.status_code)
        returned = parse_qs(urlparse(r.headers["Location"]).query)
        assert returned.get("state") == [state] and "code" in returned
        if "--session" in sys.argv:
            r = client.post(
                desktop_url + "/api/v1/auth/session/exchange",
                headers={"Origin": desktop_url},
                json={"code": returned["code"][0], "redirect": redirect, "verifier": verifier},
            )
            r.raise_for_status()
            assert "HttpOnly" in r.headers["set-cookie"] and "refresh_token" not in r.text
            restored = client.post(
                desktop_url + "/api/v1/auth/session/token", headers={"Origin": desktop_url}
            )
            restored.raise_for_status()
            user_headers = {"Authorization": "Bearer " + restored.json()["token"]}
            assert client.get(desktop_url + "/api/v1/users/me", headers=user_headers).status_code == 200
            client.post(
                desktop_url + "/api/v1/auth/session/logout", headers={"Origin": desktop_url}
            ).raise_for_status()
            assert (
                client.post(
                    desktop_url + "/api/v1/auth/session/token", headers={"Origin": desktop_url}
                ).status_code
                == 401
            )
            print(
                "PASS: offline PKCE login → server session → cookie restore → authorized API → logout invalidation"
            )
            sys.exit(0)
        r = client.post(
            auth_base + "/realms/xeonolab/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "desktop",
                "redirect_uri": redirect,
                "code": returned["code"][0],
                "code_verifier": verifier,
            },
        )
        r.raise_for_status()
        user_headers = {"Authorization": "Bearer " + r.json()["access_token"]}
        if "--uploads" in sys.argv:
            api = desktop_url + "/api/v1"
            tables = client.get(api + "/tables", headers=user_headers)
            tables.raise_for_status()
            tid = tables.json()["data"][0]["id"]
            rows = client.get(api + f"/tables/{tid}/rows", headers=user_headers)
            rows.raise_for_status()
            row_id = rows.json()["data"][0]["_row_id"]
            payload = b"multipart acceptance\n" * 900000
            started = client.post(
                api + "/uploads", headers=user_headers,
                json={"table_id": tid, "row_id": row_id, "filename": "multipart-acceptance.txt", "size": len(payload)},
            )
            started.raise_for_status()
            upload = started.json()["data"]
            upload_path = api + "/uploads/" + upload["upload_id"]
            for number in range(1, upload["part_count"] + 1):
                part = payload[(number - 1) * upload["part_size"] : number * upload["part_size"]]
                result = client.put(
                    upload_path + f"/parts/{number}", content=part,
                    headers={**user_headers, "Content-Type": "application/octet-stream"},
                )
                result.raise_for_status()
                if number == 1:
                    repeated = client.put(
                        upload_path + "/parts/1", content=part,
                        headers={**user_headers, "Content-Type": "application/octet-stream"},
                    )
                    repeated.raise_for_status()
                    assert repeated.json()["data"]["reused"] is True
            completed = client.post(upload_path + "/complete", headers=user_headers)
            completed.raise_for_status()
            aid = completed.json()["data"]["attachment_id"]
            repeated = client.post(upload_path + "/complete", headers=user_headers)
            repeated.raise_for_status()
            assert repeated.json()["data"]["attachment_id"] == aid
            downloaded = client.get(
                api + f"/attachments/{aid}/content", params={"table_id": tid}, headers=user_headers,
            )
            downloaded.raise_for_status()
            assert hashlib.sha256(downloaded.content).digest() == hashlib.sha256(payload).digest()
            client.delete(
                api + f"/tables/{tid}/rows/{row_id}/attachments/{aid}", headers=user_headers,
            ).raise_for_status()
            proof = {"bytes": len(payload), "parts": upload["part_count"], "duplicate_part_reused": True,
                     "completion_idempotent": True, "download_sha256_verified": True, "fixture_unbound": True}
            Path("runtime/verification/multipart-production.json").write_text(json.dumps(proof, indent=2))
            print("PASS: real PKCE login → multipart upload → repeated part/completion → SHA-256 download → unbind")
            sys.exit(0)
        result = client.get(desktop_url + "/api/v1/tables", headers=user_headers)
        assert result.status_code == 200 and result.json()["data"], (
            "desktop role scopes failed",
            result.status_code,
        )
        if ip_only or "--skip-llm" in sys.argv:
            print("PASS: HTTPS desktop login → public code/PKCE → signed JWT → authorized data")
            sys.exit(0)
        chat = client.post(
            "https://llm.xenon.lan/v1/chat/completions",
            headers=user_headers,
            json={
                "model": "deepseek-flash",
                "messages": [{"role": "user", "content": "Reply only OK."}],
                "max_tokens": 8,
            },
        )
        assert chat.status_code == 200 and chat.json().get("choices"), (
            "LLM gateway check failed",
            chat.status_code,
            chat.text,
        )
        print(
            "PASS: real Keycloak login → code/PKCE → signed desktop role scopes → authorized data → deepseek-flash gateway"
        )
    finally:
        client.delete(base + "/users/" + uid, headers=headers).raise_for_status()
