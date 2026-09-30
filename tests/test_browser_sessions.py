import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from xeonolab.common import transaction
from xeonolab.desktop import create_app

ORIGIN = {"Origin": "https://xenon.lan"}


def test_persistent_session_refresh_restart_logout_and_csrf(settings, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.public_key = key.public_key()
    settings.issuer = "https://identity.example/realm"
    monkeypatch.setenv("OIDC_TOKEN_URL", settings.issuer + "/token")
    calls = []
    unavailable = False
    denied = False

    original_post = httpx.Client.post

    def post(client, url, data=None, **kwargs):
        if isinstance(client, TestClient):
            return original_post(client, url, data=data, **kwargs)
        calls.append(data)
        if denied:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Offline tokens not allowed for the user or client",
                },
                request=httpx.Request("POST", url),
            )
        if unavailable:
            raise httpx.ConnectError("offline")
        token = jwt.encode(
            {
                "sub": "alice",
                "iss": settings.issuer,
                "aud": settings.audience,
                "exp": time.time() + 300,
                "scope": "desktop:read",
            },
            key,
            algorithm="RS256",
        )
        return httpx.Response(
            200,
            json={"access_token": token, "refresh_token": "rotating-secret"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    client = TestClient(create_app(settings), base_url="https://xenon.lan")
    payload = {"code": "code", "verifier": "v" * 43, "redirect": "https://xenon.lan/"}
    assert client.post("/api/v1/auth/session/exchange", json=payload).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/session/exchange", json={**payload, "redirect": "https://evil/"}, headers=ORIGIN
        ).status_code
        == 422
    )
    result = client.post("/api/v1/auth/session/exchange", json=payload, headers=ORIGIN)
    assert result.status_code == 200
    assert "refresh" not in result.text
    assert result.headers["cache-control"] == "no-store"
    cookie = result.headers["set-cookie"]
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=strict"))
    client = TestClient(create_app(settings), base_url="https://xenon.lan", cookies=client.cookies)
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 200
    assert len(calls) == 1
    with transaction(settings.data_dir / "browser-sessions.sqlite") as db:
        db.execute("UPDATE sessions SET expires=0")
    unavailable = True
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 503
    unavailable = False
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 200
    assert calls[-1]["grant_type"] == "refresh_token"
    assert client.post("/api/v1/auth/session/logout", headers={"Origin": "https://evil"}).status_code == 403
    assert client.post("/api/v1/auth/session/logout", headers=ORIGIN).status_code == 200
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 401

    denied = True
    result = client.post("/api/v1/auth/session/exchange", json=payload, headers=ORIGIN)
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "OFFLINE_ACCESS_DENIED"
