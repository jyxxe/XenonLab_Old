from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app

SCOPES = "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach deploy:read deploy:release deploy:execute"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        Path(tmp_path),
        "development",
        dev_tokens={
            "alice": {"sub": "alice", "scope": SCOPES, "agent_kind": "codex"},
            "bob": {"sub": "bob", "scope": SCOPES, "agent_kind": "codex"},
            "opencode": {"sub": "opencode", "scope": SCOPES, "agent_kind": "opencode"},
            "admin": {
                "sub": "admin",
                "scope": SCOPES + " xelibrary:admin deploy:approve",
                "agent_kind": "human",
            },
        },
    )


@pytest.fixture
def library(settings):
    return TestClient(create_app("xelibrary", settings))


def auth(token="alice", **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def put_acl(client, resource_type, resource_id, grants, token="admin"):
    path = f"/api/v1/acl/{resource_type}/{resource_id}"
    current = client.get(path, headers=auth(token))
    assert current.status_code == 200, current.text
    return client.put(
        path,
        headers=auth(token, **{"If-Match": current.headers["ETag"]}),
        json={"grants": grants},
    )
