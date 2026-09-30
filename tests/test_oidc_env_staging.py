import json
import stat

import pytest

from infra.systemd.update_oidc_maps import MAP_KEYS, NODE_SERVICES, export_payload, stage_node

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
}


def test_export_reads_only_the_two_oidc_maps(tmp_path):
    source = tmp_path / "desktop-web.env"
    source.write_text(
        "SERVICE=desktop-web\nACCOUNT_AUTH_CLIENT_SECRET_FILE=/etc/xeonolab/secret\n"
        + "\n".join(key + "='" + value + "'" for key, value in PAYLOAD.items())
        + "\n"
    )

    assert export_payload(source) == PAYLOAD


def test_staging_updates_only_allowlisted_map_variables_idempotently(tmp_path):
    expected_services = {service for services in NODE_SERVICES.values() for service in services}
    original = {
        service: "APP_MODE=production\nSERVICE=" + service + "\nOIDC_ISSUER=https://legacy.example/realm\n"
        "OIDC_ISSUERS_BY_ORIGIN_JSON='{}'\nOIDC_TOKEN_URLS_JSON='{}'\n"
        "LLM_API_KEY_FILE=/etc/xeonolab/provider-key\n"
        for service in expected_services | {"deploy-gateway"}
    }
    modes = {}
    for service, contents in original.items():
        path = tmp_path / (service + ".env")
        path.write_text(contents)
        path.chmod(0o640)
        modes[service] = stat.S_IMODE(path.stat().st_mode)

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
            "LLM_API_KEY_FILE=/etc/xeonolab/provider-key",
        ]
        assert stat.S_IMODE(path.stat().st_mode) == modes[service]
        before = path.read_bytes()
        stage_node(next(node for node, services in NODE_SERVICES.items() if service in services), PAYLOAD, tmp_path)
        assert path.read_bytes() == before

    assert (tmp_path / "deploy-gateway.env").read_text() == original["deploy-gateway"]


def test_staging_refuses_unknown_nodes_and_inconsistent_maps(tmp_path):
    with pytest.raises(ValueError, match="unsupported deployment node"):
        stage_node("109", PAYLOAD, tmp_path)
    invalid = dict(PAYLOAD)
    invalid[MAP_KEYS[1]] = "{}"
    with pytest.raises(ValueError, match="does not match"):
        stage_node("101", invalid, tmp_path)
