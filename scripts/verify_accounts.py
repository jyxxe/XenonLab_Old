"""Exercise real account creation/login/inheritance/key revocation; isolated users only."""

import base64
import hashlib
import json
import secrets
import shlex
import ssl
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx

ROOT = Path("runtime/deploy-secrets")
AUTH = "https://192.168.0.101:9444"
DESKTOP = "https://192.168.0.101:9443"
API = DESKTOP + "/api/v1"


class Form(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action, self.hidden = None, {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs.get("action")
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.hidden[attrs["name"]] = attrs.get("value", "")


def main():
    tls = ssl.create_default_context(cafile=ROOT / "lan-root-ca.crt")
    credentials = json.loads((ROOT / "credentials.json").read_text())
    users = []
    root_id = None
    with httpx.Client(verify=tls, trust_env=False, timeout=30) as admin:
        result = admin.post(
            AUTH + "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "bootstrap-admin",
                "password": credentials["keycloak_admin"],
            },
        )
        result.raise_for_status()
        headers = {"Authorization": "Bearer " + result.json()["access_token"]}
        realm = AUTH + "/admin/realms/xeonolab"

        def login(username, password):
            with httpx.Client(verify=tls, trust_env=False, timeout=30) as browser:
                verifier = secrets.token_urlsafe(48)
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                )
                state = secrets.token_urlsafe(24)
                page = browser.get(
                    AUTH + "/realms/xeonolab/protocol/openid-connect/auth",
                    params={
                        "client_id": "desktop",
                        "response_type": "code",
                        "redirect_uri": DESKTOP + "/",
                        "scope": "openid profile offline_access",
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "state": state,
                    },
                )
                page.raise_for_status()
                form = Form()
                form.feed(page.text)
                assert form.action, "login form unavailable"
                submitted = browser.post(
                    form.action, data={**form.hidden, "username": username, "password": password}
                )
                assert submitted.status_code in (302, 303), "login rejected"
                returned = parse_qs(urlparse(submitted.headers["Location"]).query)
                assert returned.get("state") == [state], (
                    "login callback failed",
                    sorted(returned.keys()),
                    returned.get("error"),
                    returned.get("error_description"),
                )
                exchanged = browser.post(
                    API + "/auth/session/exchange",
                    headers={"Origin": DESKTOP},
                    json={
                        "code": returned["code"][0],
                        "verifier": verifier,
                        "redirect": DESKTOP + "/",
                    },
                )
                exchanged.raise_for_status()
                return {"Authorization": "Bearer " + exchanged.json()["token"]}

        try:
            username = "account-verification-" + uuid4().hex[:12]
            password = secrets.token_urlsafe(32)
            created = admin.post(
                realm + "/users",
                headers=headers,
                json={
                    "username": username,
                    "enabled": True,
                    "firstName": "Account",
                    "lastName": "Verification",
                    "email": username + "@xenon.lan",
                    "emailVerified": True,
                    "credentials": [{"type": "password", "value": password, "temporary": False}],
                },
            )
            created.raise_for_status()
            root_id = str(UUID(created.headers["Location"].rsplit("/", 1)[1]))
            users.append(root_id)
            for role_name in ("platform-admin", "offline_access"):
                role = admin.get(realm + "/roles/" + role_name, headers=headers)
                role.raise_for_status()
                admin.post(
                    realm + "/users/" + root_id + "/role-mappings/realm", headers=headers, json=[role.json()]
                ).raise_for_status()
            owner = login(username, password)
            admin.post(
                API + "/auth/accounts/bootstrap", headers=owner, json={"name": "Account verification root"}
            ).raise_for_status()
            child_username = "account-child-" + uuid4().hex[:12]
            child_password = secrets.token_urlsafe(32)
            child = admin.post(
                API + "/auth/accounts",
                headers=owner,
                json={
                    "parent_id": root_id,
                    "username": child_username,
                    "name": "Account verification child",
                    "password": child_password,
                    "inherit_all": False,
                    "grants": ["desktop:read", "accounts:manage", "xelibrary:read"],
                },
            )
            child.raise_for_status()
            child_id = child.json()["data"]["id"]
            users.append(child_id)
            child_auth = login(child_username, child_password)
            identity = admin.get(API + "/users/me", headers=child_auth)
            identity.raise_for_status()
            assert set(identity.json()["data"]["scopes"]) == {
                "desktop:read",
                "accounts:manage",
                "xelibrary:read",
            }
            grand = admin.post(
                API + "/auth/accounts",
                headers=child_auth,
                json={
                    "parent_id": child_id,
                    "username": "account-grand-" + uuid4().hex[:12],
                    "name": "Account verification grandchild",
                    "password": secrets.token_urlsafe(32),
                    "inherit_all": True,
                },
            )
            grand.raise_for_status()
            grand_id = grand.json()["data"]["id"]
            users.append(grand_id)
            assert set(grand.json()["data"]["effective_scopes"]) == set(identity.json()["data"]["scopes"])
            issued = admin.post(
                API + "/auth/api-keys",
                headers=child_auth,
                json={
                    "owner": child_id,
                    "name": "isolated verification",
                    "scopes": ["desktop:read", "xelibrary:read"],
                },
            )
            issued.raise_for_status()
            kid = issued.json()["data"]["id"]
            key = {"Authorization": "Bearer " + issued.json()["data"]["token"]}
            assert admin.get(API + "/tables", headers=key).status_code == 200
            assert (
                admin.post(
                    API + "/auth/api-keys",
                    headers=key,
                    json={
                        "owner": child_id,
                        "name": "forbidden escalation",
                        "scopes": ["accounts:manage"],
                    },
                ).status_code
                == 403
            )
            shrink = admin.patch(
                API + "/auth/accounts/" + child_id,
                headers=owner,
                json={"grants": ["desktop:read", "accounts:manage"]},
            )
            shrink.raise_for_status()
            assert admin.get(API + "/tables", headers=key).status_code == 403
            inherited = admin.get(API + "/auth/accounts", headers=child_auth).json()["data"]
            assert all("xelibrary:read" not in item["effective_scopes"] for item in inherited)
            admin.delete(API + "/auth/api-keys/" + kid, headers=owner).raise_for_status()
            assert admin.get(API + "/users/me", headers=key).status_code == 401
            new_password = secrets.token_urlsafe(32)
            admin.post(
                API + "/auth/accounts/" + child_id + "/password",
                headers=owner,
                json={"password": new_password},
            ).raise_for_status()
            assert admin.get(API + "/users/me", headers=child_auth).status_code == 401
            fresh = login(child_username, new_password)
            assert admin.get(API + "/users/me", headers=fresh).status_code == 200
            admin.patch(
                API + "/auth/accounts/" + child_id, headers=owner, json={"enabled": False}
            ).raise_for_status()
            assert admin.get(API + "/users/me", headers=fresh).status_code == 401
            print(
                "PASS: real child and grandchild accounts; PKCE login; inherited permissions; API key read/attenuation/revocation; password reset and fresh login; account disable"
            )
        finally:
            # Keep audit facts but disable only the exact disposable IDs/names.
            if root_id:
                safe_ids = [str(UUID(uid)) for uid in users]
                code = (
                    "import sqlite3; db=sqlite3.connect('/var/lib/xeonolab/desktop-web/accounts.sqlite');"
                    "db.executemany(\"UPDATE accounts SET enabled=0 WHERE id=? AND name LIKE 'Account verification%'\","
                    + repr([(uid,) for uid in safe_ids])
                    + ");db.commit()"
                )
                subprocess.run(
                    [
                        "ssh",
                        "-i",
                        str(Path.home() / ".ssh/xenon_v2_ed25519"),
                        "xenon@192.168.0.101",
                        "sudo -n python3 -c " + shlex.quote(code),
                    ],
                    check=True,
                )
            for uid in reversed(users):
                deleted = admin.delete(realm + "/users/" + str(UUID(uid)), headers=headers)
                if deleted.status_code not in (204, 404):
                    raise RuntimeError("disposable identity cleanup failed")


if __name__ == "__main__":
    main()
