import json
import stat

import pytest

from xeonolab.deploy_secrets import (
    copy_forgejo_claim_inputs,
    copy_qdrant_api_key,
    validate_forgejo_claim_inputs,
)


def test_qdrant_key_copy_is_private_and_does_not_modify_source(tmp_path):
    source = tmp_path / "protected" / "qdrant-api-key"
    source.parent.mkdir()
    source.write_text("fixture-only-secret")
    destination = tmp_path / "node" / "qdrant-api-key"
    destination.parent.mkdir()

    copy_qdrant_api_key(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_forgejo_claim_inputs_require_private_files_and_valid_subject_map(tmp_path):
    token = tmp_path / "forgejo-claim-token"
    users = tmp_path / "forgejo-agent-users.json"
    token.write_text("fixture-only-secret")
    users.write_text(json.dumps({"subject-123": "general-agent"}))
    token.chmod(0o600)
    users.chmod(0o600)

    assert validate_forgejo_claim_inputs(token, users) == {"subject-123": "general-agent"}
    token.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        validate_forgejo_claim_inputs(token, users)


@pytest.mark.parametrize("invalid", ["not-json", "{}", '{"subject":"bad username"}'])
def test_forgejo_claim_inputs_reject_invalid_maps_without_echoing_contents(tmp_path, invalid):
    token = tmp_path / "forgejo-claim-token"
    users = tmp_path / "forgejo-agent-users.json"
    token.write_text("fixture-only-secret")
    users.write_text(invalid)
    token.chmod(0o600)
    users.chmod(0o600)

    with pytest.raises(ValueError) as error:
        validate_forgejo_claim_inputs(token, users)
    assert "fixture-only-secret" not in str(error.value)
    assert invalid not in str(error.value)


def test_forgejo_claim_payload_copies_protected_files(tmp_path):
    token = tmp_path / "forgejo-claim-token"
    users = tmp_path / "forgejo-agent-users.json"
    token.write_text("fixture-only-secret")
    users.write_text('{"subject-123":"general-agent"}')
    token.chmod(0o600)
    users.chmod(0o600)
    token_dest = tmp_path / "node" / "forgejo-claim-token"
    users_dest = tmp_path / "node" / "forgejo-agent-users.json"

    copy_forgejo_claim_inputs(token, users, token_dest, users_dest)

    assert stat.S_IMODE(token_dest.stat().st_mode) == stat.S_IMODE(users_dest.stat().st_mode) == 0o600
    assert token_dest.read_text() == token.read_text()
