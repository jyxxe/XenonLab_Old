import stat

from xeonolab.deploy_secrets import copy_qdrant_api_key


def test_qdrant_key_copy_is_private_and_does_not_modify_source(tmp_path):
    source = tmp_path / "protected" / "qdrant-api-key"
    source.parent.mkdir()
    source.write_text("fixture-only-secret")
    destination = tmp_path / "node" / "qdrant-api-key"
    destination.parent.mkdir()

    copy_qdrant_api_key(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
