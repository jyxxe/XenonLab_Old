"""Verify development-only Codex/OpenCode identities against the LAN APIs.

This creates one temporary Forgejo issue and matching work item, then closes and
completes them. It never creates a release or acquires a production lease.
"""

import json
import secrets
import socket
import ssl
from pathlib import Path

import httpx
import jwt

ROOT = Path("runtime/deploy-secrets")
ISSUER = "https://192.168.0.101:9444/realms/xeonolab"
API = "https://192.168.0.101:9443/api/v1"
DEPLOY = "https://deploy.xenon.lan/api/v1"
FORGEJO = "https://192.168.0.101:9445/api/v1"
REPOSITORY = "xenon-admin/XeonoLab"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    harness = json.loads((ROOT / "harness-clients.json").read_text())
    forgejo_users = json.loads((ROOT / "forgejo-users.json").read_text())
    require(set(harness) >= {"codex-dev-01", "opencode-01"}, "required OAuth clients are absent")
    require("xenon-admin" in forgejo_users, "Forgejo test account is absent")

    tls = ssl.create_default_context(cafile=ROOT / "lan-root-ca.crt")
    resolver = socket.getaddrinfo
    socket.getaddrinfo = lambda host, *args, **kwargs: resolver(
        "192.168.0.101" if str(host).endswith(".xenon.lan") else host,
        *args,
        **kwargs,
    )
    issue_auth = ("xenon-admin", forgejo_users["xenon-admin"])
    work_id = None
    issue_number = None
    codex_claim = None
    opencode_claim = None
    active_claim = None
    identities = {}
    results = []

    try:
        with httpx.Client(verify=tls, trust_env=False, timeout=45) as client:
            for name in ("codex-dev-01", "opencode-01"):
                token_response = client.post(
                    ISSUER + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": name,
                        "client_secret": harness[name],
                    },
                )
                require(token_response.status_code == 200, f"{name} OAuth token request failed")
                token = token_response.json()["access_token"]
                claims = jwt.decode(
                    token,
                    (ROOT / "oidc-public.pem").read_text(),
                    algorithms=["RS256"],
                    issuer=ISSUER,
                    audience="xeonolab",
                )
                scopes = set(claims.get("platform_scopes", "").split())
                require(not any(scope.startswith("deploy:") for scope in scopes), f"{name} has deploy scope")
                identities[name] = {
                    "subject": claims["sub"],
                    "scopes": scopes,
                    "headers": {"Authorization": "Bearer " + token},
                }
            codex = identities["codex-dev-01"]
            opencode = identities["opencode-01"]
            require(codex["subject"] != opencode["subject"], "OAuth clients share a subject")
            results.append("independent OAuth identities and no deploy scopes")

            require(
                client.get(API + "/work").status_code == 401, "unauthenticated work request was not rejected"
            )

            marker = "[Harness verification " + secrets.token_hex(8) + "]"
            issue_response = client.post(
                f"{FORGEJO}/repos/{REPOSITORY}/issues",
                auth=issue_auth,
                json={"title": marker, "body": "Temporary isolated API verification issue."},
            )
            require(issue_response.status_code == 201, "could not create temporary Forgejo issue")
            issue = issue_response.json()
            issue_number = issue["number"]
            require(issue.get("title") == marker, "Forgejo returned an unexpected issue")

            work_response = client.post(
                API + "/work",
                headers=codex["headers"],
                json={"repository": REPOSITORY, "issue_id": issue_number, "title": marker},
            )
            require(work_response.status_code == 201, "could not register isolated work item")
            work_id = work_response.json()["data"]["id"]
            work_path = API + "/work/" + work_id

            claim_response = client.post(
                work_path + "/claim", headers=codex["headers"], json={"ttl_seconds": 60}
            )
            require(claim_response.status_code == 200, "Codex could not claim isolated work")
            codex_claim = claim_response.json()["data"]
            active_claim = (codex["headers"], codex_claim["epoch"])
            epoch1 = codex_claim["epoch"]

            contender = client.post(work_path + "/claim", headers=opencode["headers"], json={})
            require(contender.status_code == 409, "second harness acquired an active claim")
            nonholder = client.post(
                work_path + "/release",
                headers=opencode["headers"],
                json={"epoch": epoch1},
            )
            require(nonholder.status_code == 409, "non-holder released another harness claim")

            stale = client.post(
                work_path + "/release",
                headers=codex["headers"],
                json={"epoch": epoch1 + 1},
            )
            require(stale.status_code == 409, "stale epoch released the work item")
            released = client.post(
                work_path + "/release",
                headers=codex["headers"],
                json={"epoch": epoch1},
            )
            require(released.status_code == 200, "current holder could not release its claim")
            active_claim = None

            claim_response = client.post(work_path + "/claim", headers=opencode["headers"], json={})
            require(claim_response.status_code == 200, "OpenCode could not claim released dev work")
            opencode_claim = claim_response.json()["data"]
            active_claim = (opencode["headers"], opencode_claim["epoch"])
            epoch2 = opencode_claim["epoch"]
            require(epoch2 == epoch1 + 1, "claim epoch did not advance")
            fenced = client.post(
                work_path + "/release",
                headers=opencode["headers"],
                json={"epoch": epoch1},
            )
            require(fenced.status_code == 409, "previous epoch was not fenced")
            results.append("exclusive work claim, non-holder denial, and epoch fencing")

            fake_release = {
                "commit": "0" * 40,
                "artifacts": [{"name": "harness-check", "digest": "sha256:" + "0" * 64}],
                "migration_manifest": "none",
                "test_report": "temporary permission check",
            }
            denied_release = client.post(DEPLOY + "/releases", headers=opencode["headers"], json=fake_release)
            require(
                denied_release.status_code == 403,
                f"OpenCode release creation returned HTTP {denied_release.status_code}",
            )
            denied_lease = client.post(
                DEPLOY + "/deploy/lease/acquire",
                headers=opencode["headers"],
                json={
                    "agent_id": opencode["subject"],
                    "release_id": "nonexistent-harness-check",
                    "ttl_seconds": 15,
                },
            )
            require(
                denied_lease.status_code == 403,
                f"OpenCode lease acquisition returned HTTP {denied_lease.status_code}",
            )
            results.append("OpenCode cannot create releases or acquire production leases")

            final = client.post(
                work_path + "/release",
                headers=opencode["headers"],
                json={"epoch": epoch2, "completed": True},
            )
            require(final.status_code == 200, "could not complete isolated work item")
            opencode_claim = None
            active_claim = None
            require(final.json()["data"]["status"] == "completed", "isolated work did not complete")

            # Close the temporary Issue before exiting. The work item is already complete.
            close = client.patch(
                f"{FORGEJO}/repos/{REPOSITORY}/issues/{issue_number}",
                auth=issue_auth,
                json={"state": "closed"},
            )
            require(close.status_code in (200, 201), "temporary Forgejo issue close failed")
            state = client.get(f"{FORGEJO}/repos/{REPOSITORY}/issues/{issue_number}", auth=issue_auth)
            require(
                state.status_code == 200 and state.json().get("state") == "closed",
                "temporary Forgejo issue did not reach closed state",
            )
            issue_number = None

    except BaseException:
        # Best-effort cleanup uses only the current claim and the dedicated temporary Issue.
        try:
            with httpx.Client(verify=tls, trust_env=False, timeout=20) as cleanup:
                if active_claim and work_id:
                    headers, epoch = active_claim
                    cleanup.post(
                        API + "/work/" + work_id + "/release",
                        headers=headers,
                        json={"epoch": epoch, "completed": True},
                    )
                elif work_id and "opencode-01" in identities:
                    headers = identities["opencode-01"]["headers"]
                    claim = cleanup.post(API + "/work/" + work_id + "/claim", headers=headers, json={})
                    if claim.status_code == 200:
                        cleanup.post(
                            API + "/work/" + work_id + "/release",
                            headers=headers,
                            json={"epoch": claim.json()["data"]["epoch"], "completed": True},
                        )
                if issue_number is not None:
                    cleanup.patch(
                        f"{FORGEJO}/repos/{REPOSITORY}/issues/{issue_number}",
                        auth=issue_auth,
                        json={"state": "closed"},
                    )
        except Exception:
            pass
        raise

    print("PASS: " + "; ".join(results) + "; temporary test Issue closed and work completed")


if __name__ == "__main__":
    main()
