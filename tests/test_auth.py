import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app


def test_oidc_signature_issuer_audience_expiry_and_dev_isolation(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    settings = Settings(
        tmp_path,
        "production",
        "https://auth.xenon.lan",
        "xeonolab",
        public,
        {"bypass": {"sub": "admin", "scope": "xelibrary:read"}},
    )
    client = TestClient(create_app("xelibrary", settings))
    claims = {
        "sub": "alice",
        "iss": settings.issuer,
        "aud": settings.audience,
        "scope": "xelibrary:read",
        "exp": int(time.time()) + 60,
    }

    def call(payload, key=private):
        token = jwt.encode(payload, key, algorithm="RS256")
        return client.get("/api/v1/tables", headers={"Authorization": "Bearer " + token})

    assert call(claims).status_code == 200
    assert call({**claims, "iss": "https://attacker"}).status_code == 401
    assert call({**claims, "aud": "another-service"}).status_code == 401
    assert call({**claims, "exp": int(time.time()) - 10}).status_code == 401
    assert call(claims, rsa.generate_private_key(public_exponent=65537, key_size=2048)).status_code == 401
    assert client.get("/api/v1/tables", headers={"Authorization": "Bearer bypass"}).status_code == 401

    public_issuer = "https://xenon.jyxxe.com/auth/realms/xeonolab"
    settings.oidc_issuers_by_origin = {"https://xenon.jyxxe.com": public_issuer}
    public_claims = {**claims, "iss": public_issuer}
    assert call(public_claims).status_code == 200
    assert call({**public_claims, "iss": "https://unlisted.example/realm"}).status_code == 401
