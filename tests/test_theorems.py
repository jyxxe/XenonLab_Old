from urllib.parse import quote

from .conftest import auth, put_acl


def theorem_body(**overrides):
    body = {
        "path": "/theorems/mathematics/analysis",
        "title": "一致收敛定理",
        "statement_natural": "一致收敛的函数列保持连续性",
        "assumptions": ["度量空间", "连续函数列"],
        "formal_status": "none",
        "confidence": {"evidence_score": 0.8, "source_score": 0.6, "consistency_score": 0.9},
        "provenance": "manual-entry",
    }
    body.update(overrides)
    return body


def create(client, token="alice", **overrides):
    response = client.post("/api/v1/theorems", headers=auth(token), json=theorem_body(**overrides))
    assert response.status_code == 201, response.text
    return response


def test_theorem_crud_revision_history_and_validation(library):
    created = create(library)
    data = created.json()["data"]
    theorem_id = data["theorem_id"]
    assert data["revision"] == 1 and data["review_status"] == "proposed"
    assert created.headers["ETag"] == '"revision:1"'
    assert data["created_by"] == "alice"
    assert data["confidence"]["evidence_score"] == 0.8

    fetched = library.get(f"/api/v1/theorems/{theorem_id}", headers=auth())
    assert fetched.status_code == 200
    assert fetched.json()["data"]["title"] == "一致收敛定理"
    revisions = library.get(f"/api/v1/theorems/{theorem_id}/revisions", headers=auth())
    assert [item["revision"] for item in revisions.json()["data"]] == [1]

    assert (
        library.patch(f"/api/v1/theorems/{theorem_id}", headers=auth(), json={"title": "改名"}).status_code
        == 422
    )
    for field, status in (("formal_status", "verified"), ("review_status", "promoted"), ("review_status", "rejected")):
        patch_status = library.patch(
            f"/api/v1/theorems/{theorem_id}",
            headers=auth(**{"If-Match": '"revision:1"'}),
            json={field: status},
        )
        assert patch_status.status_code == 422
        assert patch_status.json()["error"]["code"] == "VALIDATION_ERROR"
    updated = library.patch(
        f"/api/v1/theorems/{theorem_id}",
        headers=auth(**{"If-Match": '"revision:1"'}),
        json={"title": "一致收敛定理（修订）"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["revision"] == 2
    assert updated.headers["ETag"] == '"revision:2"'
    revisions = library.get(f"/api/v1/theorems/{theorem_id}/revisions", headers=auth()).json()["data"]
    assert [item["revision"] for item in revisions] == [1, 2]
    assert revisions[0]["title"] == "一致收敛定理"

    assert library.post("/api/v1/theorems", headers=auth(), json=theorem_body(path="/数学/其他")).status_code == 422
    assert (
        library.post("/api/v1/theorems", headers=auth(), json=theorem_body(formal_status="proved")).status_code
        == 422
    )
    for status in ("verified", "generated", "invalidated"):
        response = library.post(
            "/api/v1/theorems", headers=auth(), json=theorem_body(formal_status=status)
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    for status in ("promoted", "rejected"):
        response = library.post(
            "/api/v1/theorems", headers=auth(), json=theorem_body(review_status=status)
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (
        library.post(
            "/api/v1/theorems",
            headers=auth(),
            json=theorem_body(confidence={"evidence_score": 1.5}),
        ).status_code
        == 422
    )
    assert (
        library.post("/api/v1/theorems", headers=auth(), json={**theorem_body(), "unexpected": 1}).status_code
        == 422
    )


def test_scope_separation_promote_and_visibility(library, settings):
    theorem_id = create(library).json()["data"]["theorem_id"]
    assert library.get(f"/api/v1/theorems/{theorem_id}", headers=auth("bob")).status_code == 404
    assert (
        library.patch(
            f"/api/v1/theorems/{theorem_id}",
            headers=auth("bob", **{"If-Match": '"revision:1"'}),
            json={"title": "无权修改"},
        ).status_code
        == 404
    )
    assert library.post(f"/api/v1/theorems/{theorem_id}/promote", headers=auth()).status_code == 403

    settings.dev_tokens["admin"]["scope"] += " xelibrary:theorem:promote xelibrary:theorem:read"
    promoted = library.post(
        f"/api/v1/theorems/{theorem_id}/promote", headers=auth("admin", **{"If-Match": '"revision:1"'})
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["data"]["review_status"] == "promoted"
    assert promoted.json()["data"]["revision"] == 2
    assert (
        library.post(
            f"/api/v1/theorems/{theorem_id}/promote",
            headers=auth("admin", **{"If-Match": '"revision:2"'}),
        ).status_code
        == 409
    )

    blocked = library.patch(
        f"/api/v1/theorems/{theorem_id}",
        headers=auth(**{"If-Match": '"revision:2"'}),
        json={"title": "无 promote 权限"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "THEOREM_PROMOTED"

    draft = create(library, review_status="draft").json()["data"]["theorem_id"]
    assert (
        library.post(
            f"/api/v1/theorems/{draft}/promote", headers=auth("admin", **{"If-Match": '"revision:1"'})
        ).status_code
        == 409
    )
    forged = library.post(
        "/api/v1/theorems", headers=auth(), json=theorem_body(formal_status="invalidated")
    )
    assert forged.status_code == 422


def test_path_acl_grants_read_but_not_write(library):
    theorem_id = create(library).json()["data"]["theorem_id"]
    acl_path = "/api/v1/acl/path/" + quote("/theorems/mathematics/analysis", safe="")
    granted = library.put(
        acl_path, headers=auth("admin", **{"If-Match": '"revision:0"'}), json={"grants": {"bob": ["read"]}}
    )
    assert granted.status_code == 200
    assert library.get(f"/api/v1/theorems/{theorem_id}", headers=auth("bob")).status_code == 200
    read_only = library.patch(
        f"/api/v1/theorems/{theorem_id}",
        headers=auth("bob", **{"If-Match": '"revision:1"'}),
        json={"title": "只有读权限"},
    )
    assert read_only.status_code == 403
    assert read_only.json()["error"]["code"] == "THEOREM_READ_ONLY"
    upgraded = library.put(
        acl_path,
        headers=auth("admin", **{"If-Match": '"revision:1"'}),
        json={"grants": {"bob": ["read", "write"]}},
    )
    assert upgraded.status_code == 200
    assert (
        library.patch(
            f"/api/v1/theorems/{theorem_id}",
            headers=auth("bob", **{"If-Match": '"revision:1"'}),
            json={"title": "获得写权限"},
        ).status_code
        == 200
    )


def test_create_requires_path_write_acl_and_does_not_insert_on_denial(library):
    path = "/theorems/restricted/analysis"
    assert put_acl(
        library, "path", quote("/theorems/restricted", safe=""), {"bob": ["read"]}
    ).status_code == 200
    rejected = library.post(
        "/api/v1/theorems",
        headers=auth("bob"),
        json=theorem_body(path=path, title="不应创建的定理"),
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "THEOREM_READ_ONLY"
    visible_after_rejection = library.get(
        "/api/v1/theorems", headers=auth("admin"), params={"path": path}
    )
    assert visible_after_rejection.status_code == 200
    assert visible_after_rejection.json()["data"] == []


def test_promote_checks_visibility_and_path_write_before_if_match_and_audits(library, settings):
    theorem_id = create(library).json()["data"]["theorem_id"]
    settings.dev_tokens["bob"]["scope"] += " xelibrary:theorem:promote"
    promote = f"/api/v1/theorems/{theorem_id}/promote"
    hidden = library.post(promote, headers=auth("bob", **{"If-Match": '"revision:1"'}))
    assert hidden.status_code == 404

    acl_path = "/api/v1/acl/path/" + quote("/theorems/mathematics/analysis", safe="")
    assert put_acl(library, "path", quote("/theorems/mathematics/analysis", safe=""), {"bob": ["read"]}).status_code == 200
    read_only = library.post(promote, headers=auth("bob", **{"If-Match": '"revision:0"'}))
    assert read_only.status_code == 403
    assert read_only.json()["error"]["code"] == "THEOREM_READ_ONLY"
    upgraded = library.put(
        acl_path,
        headers=auth("admin", **{"If-Match": '"revision:1"'}),
        json={"grants": {"bob": ["read", "write"]}},
    )
    assert upgraded.status_code == 200
    stale = library.post(promote, headers=auth("bob", **{"If-Match": '"revision:0"'}))
    assert stale.status_code == 412
    promoted = library.post(promote, headers=auth("bob", **{"If-Match": '"revision:1"'}))
    assert promoted.status_code == 200
    assert promoted.headers["ETag"] == '"revision:2"'
    events = library.get("/api/v1/audit", headers=auth("admin")).json()["data"]
    assert any(event["event"] == "theorem.promoted" and event["actor"] == "bob" for event in events)


def test_reject_uses_promote_scope_and_audit(library, settings):
    theorem_id = create(library).json()["data"]["theorem_id"]
    path = f"/api/v1/theorems/{theorem_id}/reject"
    denied = library.post(path, headers=auth(**{"If-Match": '"revision:1"'}))
    assert denied.status_code == 403
    settings.dev_tokens["admin"]["scope"] += " xelibrary:theorem:promote"
    rejected = library.post(path, headers=auth("admin", **{"If-Match": '"revision:1"'}))
    assert rejected.status_code == 200
    assert rejected.json()["data"]["review_status"] == "rejected"
    assert rejected.headers["ETag"] == '"revision:2"'
    events = library.get("/api/v1/audit", headers=auth("admin")).json()["data"]
    assert any(event["event"] == "theorem.rejected" and event["actor"] == "admin" for event in events)


def test_dependencies_cycles_and_dependents(library):
    first = create(library, title="定理 A").json()["data"]["theorem_id"]
    second = create(library, title="定理 B", depends_on=[first]).json()["data"]["theorem_id"]
    dependencies = library.get(f"/api/v1/theorems/{second}/dependencies", headers=auth()).json()["data"]
    assert [item["theorem_id"] for item in dependencies] == [first]
    assert dependencies[0]["title"] == "定理 A" and dependencies[0]["missing"] is False
    dependents = library.get(f"/api/v1/theorems/{first}/dependents", headers=auth()).json()["data"]
    assert [item["theorem_id"] for item in dependents] == [second]

    missing = create(
        library,
        title="定理 C",
        depends_on=["00000000-0000-0000-0000-000000000000"],
    ).json()["data"]["theorem_id"]
    assert (
        library.get(f"/api/v1/theorems/{missing}/dependencies", headers=auth()).json()["data"][0]["missing"]
        is True
    )

    cycle = library.patch(
        f"/api/v1/theorems/{first}",
        headers=auth(**{"If-Match": '"revision:1"'}),
        json={"depends_on": [second]},
    )
    assert cycle.status_code == 422 and cycle.json()["error"]["code"] == "THEOREM_CYCLE"
    self_reference = library.patch(
        f"/api/v1/theorems/{first}",
        headers=auth(**{"If-Match": '"revision:1"'}),
        json={"depends_on": [first]},
    )
    assert self_reference.status_code == 422


def test_dependencies_hide_unreadable_target_as_missing(library):
    secret = create(library, title="隐藏定理标题", path="/theorems/private/secret").json()["data"]["theorem_id"]
    nonexistent = "00000000-0000-0000-0000-000000000000"
    source = create(
        library,
        title="可见定理",
        path="/theorems/public/source",
        depends_on=[secret, nonexistent],
    ).json()["data"]["theorem_id"]
    acl_resource = "/theorems/public"
    acl_path = "/api/v1/acl/path/" + quote(acl_resource, safe="")
    granted = library.put(
        acl_path,
        headers=auth("admin", **{"If-Match": '"revision:0"'}),
        json={"grants": {"bob": ["read"]}},
    )
    assert granted.status_code == 200
    dependencies = library.get(
        f"/api/v1/theorems/{source}/dependencies", headers=auth("bob")
    ).json()["data"]
    assert dependencies == [
        {"theorem_id": secret, "missing": True},
        {"theorem_id": nonexistent, "missing": True},
    ]


def test_promoted_evidence_requires_promote_scope_and_path_write(library, settings):
    theorem_id = create(library).json()["data"]["theorem_id"]
    settings.dev_tokens["admin"]["scope"] += " xelibrary:theorem:promote"
    promoted = library.post(
        f"/api/v1/theorems/{theorem_id}/promote",
        headers=auth("admin", **{"If-Match": '"revision:1"'}),
    )
    assert promoted.status_code == 200
    evidence = f"/api/v1/theorems/{theorem_id}/evidence"
    ordinary = library.post(
        evidence,
        headers=auth(**{"If-Match": '"revision:2"'}),
        json={"refs": ["row:unauthorized"]},
    )
    assert ordinary.status_code == 403
    settings.dev_tokens["bob"]["scope"] += " xelibrary:theorem:promote"
    acl_path = "/api/v1/acl/path/" + quote("/theorems/mathematics/analysis", safe="")
    granted = library.put(
        acl_path,
        headers=auth("admin", **{"If-Match": '"revision:0"'}),
        json={"grants": {"bob": ["read"]}},
    )
    assert granted.status_code == 200
    read_only = library.post(
        evidence,
        headers=auth("bob", **{"If-Match": '"revision:2"'}),
        json={"refs": ["row:read-only"]},
    )
    assert read_only.status_code == 403
    upgraded = library.put(
        acl_path,
        headers=auth("admin", **{"If-Match": '"revision:1"'}),
        json={"grants": {"bob": ["read", "write"]}},
    )
    assert upgraded.status_code == 200
    appended = library.post(
        evidence,
        headers=auth("bob", **{"If-Match": '"revision:2"'}),
        json={"refs": ["row:allowed"]},
    )
    assert appended.status_code == 200
    assert appended.json()["data"]["evidence_refs"] == ["row:allowed"]


def test_theorem_openapi_documents_required_if_match_and_etag(library):
    spec = library.get("/openapi.json").json()
    paths = spec["paths"]
    for path, method in (
        ("/api/v1/theorems/{theorem_id}", "patch"),
        ("/api/v1/theorems/{theorem_id}/promote", "post"),
        ("/api/v1/theorems/{theorem_id}/reject", "post"),
        ("/api/v1/theorems/{theorem_id}/evidence", "post"),
    ):
        operation = paths[path][method]
        if_match = next(p for p in operation["parameters"] if p["in"] == "header" and p["name"] == "If-Match")
        assert if_match["required"] is True
        assert "ETag" in operation["responses"]["200"]["headers"]
        assert "412" in operation["responses"]
    create_operation = paths["/api/v1/theorems"]["post"]
    assert "ETag" in create_operation["responses"]["201"]["headers"]
    assert not any(
        parameter["in"] == "header" and parameter["name"] == "If-Match"
        for parameter in create_operation.get("parameters", [])
    )
    create_schema_ref = create_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    create_schema = spec["components"]["schemas"][create_schema_ref.rsplit("/", 1)[-1]]["properties"]
    assert create_schema["formal_status"]["const"] == "none"
    assert create_schema["review_status"]["enum"] == ["draft", "proposed"]


def test_search_evidence_and_listing(library):
    first = create(library, title="一致收敛定理").json()["data"]["theorem_id"]
    second = create(
        library,
        title="群同态基本定理",
        path="/theorems/mathematics/algebra",
        statement_natural="群同态的核与像",
    ).json()["data"]["theorem_id"]

    found = library.post("/api/v1/theorems/search", headers=auth(), json={"query": "一致收敛"})
    assert [item["theorem_id"] for item in found.json()["data"]] == [first]
    by_path = library.post(
        "/api/v1/theorems/search",
        headers=auth(),
        json={"query": "群", "path": "/theorems/mathematics/algebra"},
    )
    assert [item["theorem_id"] for item in by_path.json()["data"]] == [second]
    excluded = library.post(
        "/api/v1/theorems/search",
        headers=auth(),
        json={"query": "一致收敛", "path": "/theorems/computer-science/**"},
    )
    assert excluded.json()["data"] == []
    assert (
        library.post(
            "/api/v1/theorems/search", headers=auth(), json={"query": "群", "review_status": "nonsense"}
        ).status_code
        == 422
    )

    listed = library.get("/api/v1/theorems", headers=auth(), params={"path": "/theorems/**", "limit": 1})
    assert len(listed.json()["data"]) == 1 and listed.json()["meta"]["next_cursor"]
    rest = library.get(
        "/api/v1/theorems", headers=auth(), params={"limit": 1, "cursor": listed.json()["meta"]["next_cursor"]}
    )
    assert [item["theorem_id"] for item in rest.json()["data"]] != [
        listed.json()["data"][0]["theorem_id"]
    ]
    assert (
        library.get("/api/v1/theorems", headers=auth(), params={"review_status": "nonsense"}).status_code == 422
    )

    assert (
        library.post(f"/api/v1/theorems/{first}/evidence", headers=auth(), json={"refs": ["row:abc"]}).status_code
        == 422
    )
    appended = library.post(
        f"/api/v1/theorems/{first}/evidence",
        headers=auth(**{"If-Match": '"revision:1"'}),
        json={"refs": ["row:abc", "row:def"]},
    )
    assert appended.status_code == 200 and appended.json()["data"]["revision"] == 2
    deduped = library.post(
        f"/api/v1/theorems/{first}/evidence",
        headers=auth(**{"If-Match": '"revision:2"'}),
        json={"refs": ["row:abc"]},
    )
    assert deduped.json()["data"]["evidence_refs"] == ["row:abc", "row:def"]
    evidence = library.get(f"/api/v1/theorems/{first}/evidence", headers=auth()).json()["data"]
    assert evidence["evidence_refs"] == ["row:abc", "row:def"] and evidence["revision"] == 3


def test_verify_candidates_fail_closed_metrics_and_audit(library, settings):
    theorem_id = create(library).json()["data"]["theorem_id"]
    verify = library.post(f"/api/v1/theorems/{theorem_id}/verify", headers=auth())
    assert verify.status_code == 503
    assert verify.json()["error"]["code"] == "LEAN_NOT_CONFIGURED"
    candidates = library.post(f"/api/v1/theorems/{theorem_id}/candidates", headers=auth())
    assert candidates.status_code == 503
    assert candidates.json()["error"]["code"] == "AGENT_NOT_CONFIGURED"

    if hasattr(library.app.state, "metrics"):
        text = library.get("/metrics").text
        assert 'xelibrary_theorems{review_status="proposed"} 1' in text
        assert 'xelibrary_theorems{review_status="promoted"} 0' in text

    settings.dev_tokens["admin"]["scope"] += " xelibrary:theorem:read"
    events = library.get("/api/v1/audit", headers=auth("admin")).json()["data"]
    assert "theorem.created" in [event["event"] for event in events]
