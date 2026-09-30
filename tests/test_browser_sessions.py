import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from xeonolab.browser_sessions import _token_endpoint
from xeonolab.common import APIError, transaction
from xeonolab.desktop import create_app

ORIGIN = {"Origin": "https://xenon.lan"}


def test_persistent_session_refresh_restart_logout_and_csrf(settings, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.public_key = key.public_key()
    settings.issuer = "https://identity.example/realm"
    monkeypatch.setenv("OIDC_TOKEN_URL", settings.issuer + "/protocol/openid-connect/token")
    calls = []
    unavailable = False
    denied = False
    malformed_refresh = False

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
        if malformed_refresh:
            return httpx.Response(
                400,
                json={"error": "invalid_client", "error_description": "provider temporarily rejected the client"},
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
    with transaction(settings.data_dir / "browser-sessions.sqlite") as db:
        db.execute("UPDATE sessions SET expires=0")
    malformed_refresh = True
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 503
    with transaction(settings.data_dir / "browser-sessions.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    malformed_refresh = False
    # A new app/client process can still refresh after temporary provider failure.
    client = TestClient(create_app(settings), base_url="https://xenon.lan", cookies=client.cookies)
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 200
    with transaction(settings.data_dir / "browser-sessions.sqlite") as db:
        db.execute("UPDATE sessions SET expires=0")
    denied = True
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 401
    with transaction(settings.data_dir / "browser-sessions.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert client.post("/api/v1/auth/session/logout", headers={"Origin": "https://evil"}).status_code == 403
    assert client.post("/api/v1/auth/session/logout", headers=ORIGIN).status_code == 200
    assert client.post("/api/v1/auth/session/token", headers=ORIGIN).status_code == 401


def test_origin_selects_exact_public_or_lan_issuer(settings, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.public_key = key.public_key()
    lan_issuer = "https://192.168.0.101:9444/realms/xeonolab"
    public_issuer = "https://xenon.jyxxe.com/auth/realms/xeonolab"
    settings.issuer = lan_issuer
    settings.oidc_issuers_by_origin = {
        "https://xenon.jyxxe.com": public_issuer,
        "https://192.168.0.101:9443": lan_issuer,
    }
    settings.oidc_token_urls = {
        lan_issuer: lan_issuer + "/protocol/openid-connect/token",
        public_issuer: public_issuer + "/protocol/openid-connect/token",
    }
    monkeypatch.delenv("OIDC_TOKEN_URL", raising=False)
    endpoints = {url: issuer for issuer, url in settings.oidc_token_urls.items()}
    original_post = httpx.Client.post

    def post(client, url, data=None, **kwargs):
        if isinstance(client, TestClient):
            return original_post(client, url, data=data, **kwargs)
        issuer = endpoints[url].removesuffix("/protocol/openid-connect/token")
        token = jwt.encode(
            {
                "sub": "alice",
                "iss": issuer,
                "aud": settings.audience,
                "exp": time.time() + 300,
                "scope": "desktop:read",
            },
            key,
            algorithm="RS256",
        )
        return httpx.Response(
            200,
            json={"access_token": token, "refresh_token": "refresh-value"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    client = TestClient(create_app(settings), base_url="https://xenon.jyxxe.com")
    public_config = client.get(
        "/api/v1/auth/config", params={"origin": "https://xenon.jyxxe.com"}
    )
    ip_config = client.get(
        "/api/v1/auth/config", params={"origin": "https://192.168.0.101:9443"}
    )
    assert public_config.json()["issuer"] == public_issuer
    assert ip_config.json()["issuer"] == lan_issuer
    assert client.get("/api/v1/auth/config", params={"origin": "https://attacker.example"}).status_code == 403

    for origin, issuer in (
        ("https://xenon.jyxxe.com", public_issuer),
        ("https://192.168.0.101:9443", lan_issuer),
    ):
        client.cookies.clear()
        result = client.post(
            "/api/v1/auth/session/exchange",
            headers={"Origin": origin},
            json={"code": "one-time-code", "verifier": "v" * 43, "redirect": origin + "/"},
        )
        assert result.status_code == 200, result.text

    with transaction(settings.data_dir / "browser-sessions.sqlite") as db:
        assert {row[0] for row in db.execute("SELECT DISTINCT issuer FROM sessions")} == {
            public_issuer,
            lan_issuer,
        }


def test_exchange_rejects_token_issued_for_other_configured_origin(settings, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.public_key = key.public_key()
    lan_issuer = "https://192.168.0.101:9444/realms/xeonolab"
    public_issuer = "https://xenon.jyxxe.com/auth/realms/xeonolab"
    settings.issuer = lan_issuer
    settings.oidc_issuers_by_origin = {"https://xenon.jyxxe.com": public_issuer}
    settings.oidc_token_urls = {public_issuer: public_issuer + "/protocol/openid-connect/token"}
    original_post = httpx.Client.post

    def post(client, url, data=None, **kwargs):
        if isinstance(client, TestClient):
            return original_post(client, url, data=data, **kwargs)
        token = jwt.encode(
            {
                "sub": "alice",
                "iss": lan_issuer,
                "aud": settings.audience,
                "exp": time.time() + 300,
                "scope": "desktop:read",
            },
            key,
            algorithm="RS256",
        )
        return httpx.Response(
            200,
            json={"access_token": token, "refresh_token": "refresh-value"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    client = TestClient(create_app(settings), base_url="https://xenon.jyxxe.com")
    response = client.post(
        "/api/v1/auth/session/exchange",
        headers={"Origin": "https://xenon.jyxxe.com"},
        json={
            "code": "one-time-code",
            "verifier": "v" * 43,
            "redirect": "https://xenon.jyxxe.com/",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"


def test_preexisting_session_rows_are_pinned_to_legacy_issuer(settings):
    settings.issuer = "https://192.168.0.101:9444/realms/xeonolab"
    path = settings.data_dir / "browser-sessions.sqlite"
    with transaction(path) as db:
        db.execute("""CREATE TABLE sessions(
            id TEXT PRIMARY KEY, subject TEXT NOT NULL, refresh TEXT NOT NULL,
            access TEXT NOT NULL, expires REAL NOT NULL)""")
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", ("sid", "alice", "refresh", "access", 123))

    create_app(settings)

    with transaction(path) as db:
        assert "issuer" in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        assert db.execute("SELECT issuer FROM sessions WHERE id='sid'").fetchone()[0] == settings.issuer


def test_refresh_endpoint_cannot_be_selected_by_untrusted_session_data(settings):
    app = create_app(settings)
    with pytest.raises(APIError, match="token endpoint unavailable"):
        _token_endpoint(app, "https://attacker.example/realms/xeonolab")
