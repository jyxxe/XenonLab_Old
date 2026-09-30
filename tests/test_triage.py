from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth

REPOSITORY = "owner/repo"


def register(client, number, title, repository=REPOSITORY):
    result = client.post(
        "/api/v1/work",
        headers=auth(),
        json={"repository": repository, "issue_id": number, "title": title},
    )
    assert result.status_code in (200, 201), result.text
    return result.json()["data"]


def triage(client, number, token="alice", repository=REPOSITORY):
    return client.post(
        f"/api/v1/issues/{number}/triage",
        headers=auth(token),
        json={"repository": repository},
    )


def dev_client(settings):
    for user in ("alice", "bob", "opencode"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    return TestClient(create_app("dev-agent", settings))


def test_triage_rules_are_deterministic(settings):
    client = dev_client(settings)
    cases = {
        1: ("Implement semantic search", ["search"], "normal", "codex"),
        2: ("Add deploy rollback safeguards", ["deploy"], "high", "codex"),
        3: ("Update the operations runbook", ["docs"], "low", "opencode"),
        4: ("Perform failure-injection test", ["infra"], "high", "opencode"),
        5: ("OpenWebUI XeLibrary tool gateway", ["xelibrary", "desktop"], "normal", "codex"),
        6: ("Refactor widget layout", [], "normal", "codex"),
    }
    for number, (title, _, _, _) in cases.items():
        register(client, number, title)
    for number, (_, labels, priority, agent) in cases.items():
        data = triage(client, number).json()["data"]
        assert data["labels"] == labels
        assert data["priority"] == priority
        assert data["recommended_agent"] == agent
        assert data["rule_version"] == "triage-rules-v1"
        assert data["issue_id"] == number


def test_triage_detects_duplicate_and_related_titles(settings):
    client = dev_client(settings)
    register(client, 10, "Fix login regression for imported users")
    register(client, 11, "Fix login regression, imported users")
    register(client, 20, "Implement semantic search")
    register(client, 21, "Integrate Qdrant fast_text collection for semantic search")
    first = triage(client, 10).json()["data"]
    assert first["duplicates"] == [11]
    assert first["related"] == []
    second = triage(client, 20).json()["data"]
    assert second["duplicates"] == []
    assert second["related"] == [21]
    assert triage(client, 20).json()["data"] == second
    restarted = TestClient(create_app("dev-agent", settings))
    assert triage(restarted, 20).json()["data"] == second


def test_triage_merges_link_graph_and_isolates_repositories(settings):
    client = dev_client(settings)
    register(client, 30, "Investigate flaky index")
    register(client, 31, "Unrelated task about tomatoes")
    register(client, 30, "Other repository issue", repository="other/repo")
    body = {"repository": REPOSITORY, "target_issue_id": 31, "type": "related_to"}
    assert client.post("/api/v1/issues/30/links", headers=auth(), json=body).status_code == 201
    data = triage(client, 30).json()["data"]
    assert data["related"] == [31]
    assert data["duplicates"] == []
    isolated = triage(client, 30, repository="other/repo").json()["data"]
    assert isolated["related"] == []
    assert isolated["duplicates"] == []


def test_triage_requires_dev_scope_and_registered_issue(settings):
    client = dev_client(settings)
    register(client, 40, "Some issue")
    assert client.post("/api/v1/issues/40/triage", json={"repository": REPOSITORY}).status_code == 401
    assert triage(client, 40, token="admin").status_code == 403
    assert triage(client, 41).status_code == 404
    assert triage(client, 0).status_code == 422
    invalid = client.post("/api/v1/issues/40/triage", headers=auth(), json={"repository": "not-a-repo"})
    assert invalid.status_code == 422


def test_triage_treats_titles_as_inert_data(settings):
    client = dev_client(settings)
    hostile = '"; DROP TABLE work;-- <script>alert(1)</script> [Plan x]'
    register(client, 50, hostile)
    result = triage(client, 50)
    assert result.status_code == 200
    assert result.json()["data"]["title"] == hostile
    assert triage(client, 50).json()["data"] == result.json()["data"]
    listing = client.get("/api/v1/work", headers=auth())
    assert listing.status_code == 200 and len(listing.json()["data"]) == 1
