import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from infra.systemd.update_oidc_maps import (
    MAP_KEYS,
    NODE_SERVICES,
    ROLE_SCOPES_KEY,
    export_payload,
    stage_node,
)
from xeonolab.role_scopes import OIDC_ROLE_SCOPES

ISSUERS = {
    "https://xenon.jyxxe.com": "https://xenon.jyxxe.com/auth/realms/xeonolab",
    "https://xenon.lan": "https://auth.xenon.lan/realms/xeonolab",
    "https://192.168.0.101:9443": "https://192.168.0.101:9444/realms/xeonolab",
}
PAYLOAD = {
    MAP_KEYS[0]: json.dumps(ISSUERS, separators=(",", ":")),
    MAP_KEYS[1]: json.dumps(
        {issuer: issuer + "/protocol/openid-connect/token" for issuer in ISSUERS.values()},
        separators=(",", ":"),
    ),
    ROLE_SCOPES_KEY: json.dumps(
        {role: list(scopes) for role, scopes in OIDC_ROLE_SCOPES.items()}, separators=(",", ":")
    ),
}


def test_export_reads_the_shared_oidc_maps_and_role_policy(tmp_path):
    source = tmp_path / "desktop-web.env"
    source.write_text(
        "SERVICE=desktop-web\nACCOUNT_AUTH_CLIENT_SECRET_FILE=/etc/xeonolab/secret\n"
        + "\n".join(key + "='" + value + "'" for key, value in PAYLOAD.items())
        + "\n"
    )

    assert export_payload(source) == PAYLOAD


def test_export_cli_loads_shared_policy_outside_the_checkout(tmp_path):
    source = tmp_path / "desktop-web.env"
    source.write_text("\n".join(key + "='" + value + "'" for key, value in PAYLOAD.items()) + "\n")
    output = tmp_path / "payload.json"
    script = Path(__file__).resolve().parents[1] / "infra/systemd/update_oidc_maps.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--export-env", str(source), "--output", str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text()) == PAYLOAD


def test_staging_updates_only_allowlisted_map_variables_idempotently(tmp_path):
    expected_services = {service for services in NODE_SERVICES.values() for service in services}
    original = {
        service: "APP_MODE=production\nSERVICE=" + service + "\nOIDC_ISSUER=https://legacy.example/realm\n"
        "OIDC_ISSUERS_BY_ORIGIN_JSON='{}'\nOIDC_TOKEN_URLS_JSON='{}'\n"
        "OIDC_ROLE_SCOPES_JSON='{}'\n"
        "LLM_API_KEY_FILE=/etc/xeonolab/provider-key\n"
        for service in expected_services | {"deploy-gateway"}
    }
    modes = {}
    owners = {}
    for service, contents in original.items():
        path = tmp_path / (service + ".env")
        path.write_text(contents)
        path.chmod(0o640)
        metadata = path.stat()
        modes[service] = stat.S_IMODE(metadata.st_mode)
        owners[service] = (metadata.st_uid, metadata.st_gid)

    for node in NODE_SERVICES:
        assert set(stage_node(node, PAYLOAD, tmp_path)) == set(NODE_SERVICES[node])

    for service in expected_services:
        path = tmp_path / (service + ".env")
        content = path.read_text()
        assert content.splitlines() == [
            "APP_MODE=production",
            "SERVICE=" + service,
            "OIDC_ISSUER=https://legacy.example/realm",
            MAP_KEYS[0] + "='" + PAYLOAD[MAP_KEYS[0]] + "'",
            MAP_KEYS[1] + "='" + PAYLOAD[MAP_KEYS[1]] + "'",
            ROLE_SCOPES_KEY + "='" + PAYLOAD[ROLE_SCOPES_KEY] + "'",
            "LLM_API_KEY_FILE=/etc/xeonolab/provider-key",
        ]
        assert stat.S_IMODE(path.stat().st_mode) == modes[service]
        assert (path.stat().st_uid, path.stat().st_gid) == owners[service]
        before = path.read_bytes()
        stage_node(next(node for node, services in NODE_SERVICES.items() if service in services), PAYLOAD, tmp_path)
        assert path.read_bytes() == before

    deploy_gateway = (tmp_path / "deploy-gateway.env").read_text()
    assert ROLE_SCOPES_KEY + "='" + PAYLOAD[ROLE_SCOPES_KEY] + "'" in deploy_gateway
    assert "LLM_API_KEY_FILE=/etc/xeonolab/provider-key" in deploy_gateway


def test_staging_refuses_unknown_nodes_and_inconsistent_maps(tmp_path):
    with pytest.raises(ValueError, match="unsupported deployment node"):
        stage_node("109", PAYLOAD, tmp_path)
    invalid = dict(PAYLOAD)
    invalid[MAP_KEYS[1]] = "{}"
    with pytest.raises(ValueError, match="does not match"):
        stage_node("101", invalid, tmp_path)


def test_staging_rejects_unknown_payload_fields_and_unapproved_role_scopes():
    invalid = dict(PAYLOAD, OIDC_UNEXPECTED_JSON="{}")
    with pytest.raises(ValueError, match="unexpected schema"):
        stage_node("101", invalid)

    role_map = json.loads(PAYLOAD[ROLE_SCOPES_KEY])
    role_map["platform-admin"].append("not-a-real-scope")
    invalid = dict(PAYLOAD, **{ROLE_SCOPES_KEY: json.dumps(role_map)})
    with pytest.raises(ValueError, match="shared role policy"):
        stage_node("101", invalid)


def test_staging_rejects_attacker_issuer_even_with_matching_token_url():
    invalid = dict(PAYLOAD)
    issuers = dict(ISSUERS)
    issuers["https://xenon.lan"] = "https://attacker.example/realms/xeonolab"
    invalid[MAP_KEYS[0]] = json.dumps(issuers, separators=(",", ":"))
    invalid[MAP_KEYS[1]] = json.dumps(
        {issuer: issuer + "/protocol/openid-connect/token" for issuer in issuers.values()},
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="exact expected issuers"):
        stage_node("101", invalid)
