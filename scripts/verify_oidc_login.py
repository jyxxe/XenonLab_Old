"""Verify real browser-code PKCE semantics with a disposable realm user."""

import base64
import hashlib
import json
import secrets
import socket
import ssl
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
desktop_url = "https://192.168.0.101:9443" if ip_only else "https://xenon.jyxxe.com"

original = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **kw: original(
    "192.168.0.101" if str(host).endswith("xenon.lan") else host, *a, **kw
)
root = Path("runtime/deploy-secrets")
credentials = json.loads((root / "credentials.json").read_text())
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
context.load_default_certs()
with httpx.Client(verify=context, trust_env=False, timeout=90) as client:
    if ip_only:
        config = client.get(desktop_url + "/api/v1/auth/config").json()
        assert config["issuer"] == "https://xenon.jyxxe.com/auth/realms/xeonolab"
        discovery = client.get(config["issuer"] + "/.well-known/openid-configuration").json()
        assert discovery["issuer"] == config["issuer"]
        assert urlparse(discovery["authorization_endpoint"]).hostname == "xenon.jyxxe.com"
    r = client.post(
        "https://xenon.jyxxe.com/auth/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "bootstrap-admin",
            "password": credentials["keycloak_admin"],
        },
    )
    r.raise_for_status()
    headers = {"Authorization": "Bearer " + r.json()["access_token"]}
    base = "https://xenon.jyxxe.com/auth/admin/realms/xeonolab"
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
            "https://xenon.jyxxe.com/auth/realms/xeonolab/protocol/openid-connect/auth",
            params={
                "client_id": "desktop",
                "response_type": "code",
                "redirect_uri": redirect,
                "scope": "openid profile",
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
            assert urlparse(form.action).hostname == "xenon.jyxxe.com"
        r = client.post(form.action, data={**form.hidden, "username": username, "password": password})
        assert r.status_code in (302, 303), ("login did not finish", r.status_code)
        returned = parse_qs(urlparse(r.headers["Location"]).query)
        assert returned.get("state") == [state] and "code" in returned
        r = client.post(
            "https://xenon.jyxxe.com/auth/realms/xeonolab/protocol/openid-connect/token",
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
