from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth

MANIFEST = {
    "id": "hello-plugin",
    "name": "Hello plugin",
    "version": "1.0.0",
    "description": "Example declarative plugin",
    "author": "OpenCode",
    "icon": "◈",
    "permissions": ["xelibrary:read"],
    "app": {
        "id": "hello-app",
        "title": "Hello app",
        "origin": "https://hello.example",
        "entry": "/",
        "icon": "◈",
        "default_size": [900, 600],
        "singleton": True,
        "sandbox": {"allow_scripts": True, "allow_forms": True, "allow_downloads": False},
    },
    "widget": {
        "id": "hello-widget",
        "title": "Hello widget",
        "origin": "https://hello.example",
        "entry": "/widget",
        "icon": "◈",
        "height": 180,
    },
}


def desktop_settings(settings):
    for token in ("alice", "bob", "admin"):
        if "desktop:read" not in settings.dev_tokens[token]["scope"]:
            settings.dev_tokens[token]["scope"] += " desktop:read"
    if "desktop:admin" not in settings.dev_tokens["admin"]["scope"]:
        settings.dev_tokens["admin"]["scope"] += " desktop:admin"
    return settings


def publish(client, body=None):
    return client.post("/api/v1/plugins", headers=auth("admin"), json=body or MANIFEST)


def test_plugin_catalog_requires_admin_and_validates_manifests(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert client.post("/api/v1/plugins", headers=auth(), json=MANIFEST).status_code == 403
    assert client.get("/api/v1/plugins", headers=auth()).status_code == 200

    invalid = [
        {**MANIFEST, "id": "Bad_ID"},
        {**MANIFEST, "version": "1.0"},
        {**MANIFEST, "permissions": ["xelibrary:read", "xelibrary:read"]},
        {**MANIFEST, "homepage": "http://insecure.example"},
        {**MANIFEST, "app": {**MANIFEST["app"], "origin": "javascript:alert(1)"}},
        {**MANIFEST, "app": {**MANIFEST["app"], "id": "chat"}},
        {**MANIFEST, "app": {**MANIFEST["app"], "id": "knowledge"}},
        {**MANIFEST, "app": {**MANIFEST["app"], "id": "openwebui"}},
        {**MANIFEST, "app": None, "widget": None},
        {**MANIFEST, "app": {**MANIFEST["app"], "entry": "//attacker"}},
    ]
    for body in invalid:
        assert publish(client, body).status_code == 422, body

    created = publish(client)
    assert created.status_code == 201, created.text
    item = created.json()["data"]
    assert item["app"]["origin"] == "https://hello.example"
    assert item["widget"]["origin"] == "https://hello.example"
    assert "default_size" not in item["widget"]
    assert item["installed"] is False and item["revision"] == 1
    assert publish(client).json()["error"]["code"] == "PLUGIN_EXISTS"

    assert client.post(
        "/api/v1/apps", headers=auth("admin"), json={"id": "hello-app", "title": "Collision", "origin": "https://other.example"}
    ).json()["error"]["code"] == "APP_EXISTS"
    listing = client.get("/api/v1/plugins", headers=auth("bob")).json()["data"]
    assert [entry["id"] for entry in listing] == ["hello-plugin"]


def test_plugin_install_is_per_account_and_projects_into_apps_and_widgets(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert publish(client).status_code == 201

    installed = client.post("/api/v1/plugins/hello-plugin/install", headers=auth())
    assert installed.status_code == 200, installed.text
    assert installed.json()["data"]["installed"] is True
    assert installed.json()["data"]["installed_version"] == "1.0.0"
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200

    alice_apps = client.get("/api/v1/apps", headers=auth()).json()["data"]
    plugin_app = next(entry for entry in alice_apps if entry["id"] == "hello-app")
    assert plugin_app["plugin_id"] == "hello-plugin"
    assert plugin_app["plugin_version"] == "1.0.0"
    assert plugin_app["permissions"] == ["xelibrary:read"]
    assert plugin_app["description"] == "Example declarative plugin"
    assert client.get("/api/v1/apps/hello-app", headers=auth()).status_code == 200
    assert client.get("/api/v1/apps/hello-app", headers=auth("bob")).status_code == 404

    admin_registry = client.get("/api/v1/apps?include_unavailable=true", headers=auth("admin")).json()["data"]
    assert all(entry["id"] != "hello-app" for entry in admin_registry)

    widgets = client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"]
    assert [(widget["plugin_id"], widget["id"], widget["height"]) for widget in widgets] == [
        ("hello-plugin", "hello-widget", 180)
    ]
    assert client.get("/api/v1/users/me/plugin-widgets", headers=auth("bob")).json()["data"] == []

    disabled = client.patch(
        "/api/v1/plugins/hello-plugin/install", headers=auth(), json={"enabled": False}
    )
    assert disabled.status_code == 200 and disabled.json()["data"]["enabled"] is False
    assert client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"] == []
    assert all(
        entry["id"] != "hello-app" for entry in client.get("/api/v1/apps", headers=auth()).json()["data"]
    )
    assert client.patch(
        "/api/v1/plugins/hello-plugin/install", headers=auth(), json={"enabled": True}
    ).json()["data"]["enabled"] is True

    preferences = client.patch(
        "/api/v1/users/me/preferences", headers=auth(), json={"plugin_widgets": ["hello-plugin"]}
    )
    assert preferences.status_code == 200
    assert preferences.json()["data"]["plugin_widgets"] == ["hello-plugin"]
    assert client.get("/api/v1/users/me/preferences", headers=auth()).json()["data"]["plugin_widgets"] == [
        "hello-plugin"
    ]

    removed = client.delete("/api/v1/plugins/hello-plugin/install", headers=auth())
    assert removed.status_code == 200 and removed.json()["data"]["deleted"] is True
    assert client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"] == []
    cleared = client.get("/api/v1/users/me/preferences", headers=auth()).json()["data"]
    assert cleared["plugin_widgets"] == []
    assert client.delete("/api/v1/plugins/hello-plugin/install", headers=auth()).json()["data"]["deleted"] is False


def test_plugin_install_requires_current_scopes_and_unknown_plugins_are_404(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    restricted = {**MANIFEST, "id": "admin-only", "app": {**MANIFEST["app"], "id": "admin-only-app"}}
    restricted["permissions"] = ["desktop:admin"]
    assert publish(client, restricted).status_code == 201
    denied = client.post("/api/v1/plugins/admin-only/install", headers=auth())
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"missing_scopes": ["desktop:admin"]}
    assert client.post("/api/v1/plugins/admin-only/install", headers=auth("admin")).status_code == 200
    admin_widgets = client.get("/api/v1/users/me/plugin-widgets", headers=auth("admin")).json()["data"]
    assert [widget["plugin_id"] for widget in admin_widgets] == ["admin-only"]

    for method, path, body in (
        ("get", "/api/v1/plugins/missing", None),
        ("post", "/api/v1/plugins/missing/install", None),
        ("delete", "/api/v1/plugins/missing/install", None),
        ("patch", "/api/v1/plugins/missing/install", {"enabled": True}),
    ):
        response = getattr(client, method)(path, headers=auth(), **({"json": body} if body else {}))
        assert response.status_code == 404, (method, path, response.text)


def test_plugin_catalog_updates_use_etags_and_delete_cleans_installs(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert publish(client).status_code == 201
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    assert client.post(
        "/api/v1/apps", headers=auth("admin"), json={"id": "taken-app", "title": "Taken", "origin": "https://taken.example"}
    ).status_code == 201
    current = client.get("/api/v1/plugins/hello-plugin", headers=auth())
    revision = current.headers["etag"]

    updated_body = {
        **MANIFEST,
        "version": "2.0.0",
        "app": {**MANIFEST["app"], "id": "hello-app-v2", "origin": "https://v2.hello.example"},
    }
    assert client.put("/api/v1/plugins/hello-plugin", headers=auth("admin"), json=updated_body).status_code == 412
    conflict = {**updated_body, "app": {**updated_body["app"], "id": "taken-app"}}
    assert client.put(
        "/api/v1/plugins/hello-plugin",
        headers=auth("admin", **{"If-Match": revision}),
        json=conflict,
    ).status_code == 409
    updated = client.put(
        "/api/v1/plugins/hello-plugin",
        headers=auth("admin", **{"If-Match": revision}),
        json=updated_body,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["revision"] == 2 and updated.json()["data"]["version"] == "2.0.0"

    listing = client.get("/api/v1/plugins", headers=auth()).json()["data"][0]
    assert listing["update_available"] is True and listing["installed_version"] == "1.0.0"
    # Catalogue edits do not silently move an installed app: the old snapshot is
    # still projected until the account explicitly reinstalls.
    assert client.get("/api/v1/apps/hello-app", headers=auth()).status_code == 200
    assert client.get("/api/v1/apps/hello-app-v2", headers=auth()).status_code == 404
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    assert client.get("/api/v1/plugins", headers=auth()).json()["data"][0]["update_available"] is False
    assert client.get("/api/v1/apps/hello-app-v2", headers=auth()).status_code == 200
    assert client.get("/api/v1/apps/hello-app", headers=auth()).status_code == 404

    assert client.patch(
        "/api/v1/users/me/preferences", headers=auth(), json={"plugin_widgets": ["hello-plugin"]}
    ).status_code == 200
    current = client.get("/api/v1/plugins/hello-plugin", headers=auth())
    assert client.delete(
        "/api/v1/plugins/hello-plugin", headers=auth("admin", **{"If-Match": current.headers["etag"]})
    ).status_code == 200
    assert client.get("/api/v1/plugins", headers=auth()).json()["data"] == []
    assert client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"] == []
    assert client.get("/api/v1/users/me/preferences", headers=auth()).json()["data"]["plugin_widgets"] == []
    assert [entry["id"] for entry in client.get("/api/v1/apps", headers=auth()).json()["data"]] == ["taken-app"]
    assert client.post(
        "/api/v1/apps", headers=auth("admin"), json={"id": "hello-app-v2", "title": "Now free", "origin": "https://free.example"}
    ).status_code == 201


def test_catalog_permission_changes_need_an_explicit_scope_checked_update(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert publish(client).status_code == 201
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    current = client.get("/api/v1/plugins/hello-plugin", headers=auth())
    escalated = {**MANIFEST, "version": "1.1.0", "permissions": ["custom:secret"]}
    assert client.put(
        "/api/v1/plugins/hello-plugin",
        headers=auth("admin", **{"If-Match": current.headers["etag"]}),
        json=escalated,
    ).status_code == 200
    # The old snapshot keeps working; the escalated catalogue version cannot be
    # installed until the account actually holds the new scope.
    assert client.get("/api/v1/apps/hello-app", headers=auth()).status_code == 200
    denied = client.post("/api/v1/plugins/hello-plugin/install", headers=auth())
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"missing_scopes": ["custom:secret"]}
    item = client.get("/api/v1/plugins", headers=auth()).json()["data"][0]
    assert item["installed_version"] == "1.0.0" and item["update_available"] is True
    assert item["missing_scopes"] == ["custom:secret"]


def test_plugin_widget_preferences_are_validated_and_persist(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    url = "/api/v1/users/me/preferences"
    saved = client.patch(url, headers=auth(), json={"plugin_widgets": ["hello-plugin"]}).json()["data"]
    assert saved["plugin_widgets"] == ["hello-plugin"]
    restarted = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert restarted.get(url, headers=auth()).json()["data"]["plugin_widgets"] == ["hello-plugin"]
    for invalid in (
        {"plugin_widgets": ["hello-plugin", "hello-plugin"]},
        {"plugin_widgets": ["Bad_ID"]},
        {"plugin_widgets": ["x"] * 9},
        {"plugin_widgets": "hello-plugin"},
    ):
        assert client.patch(url, headers=auth(), json=invalid).status_code == 422


def test_plugin_routes_are_in_the_desktop_openapi(settings):
    document = create_app("desktop-web", desktop_settings(settings)).openapi()
    assert "/api/v1/plugins" in document["paths"]
    assert "/api/v1/plugins/{plugin_id}/install" in document["paths"]
    assert "/api/v1/users/me/plugin-widgets" in document["paths"]
    schema = document["components"]["schemas"]["PluginManifest"]
    assert set(schema["required"]) >= {"id", "name", "version"}


def test_installed_snapshot_keeps_its_app_id_reserved(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    alpha = {**MANIFEST, "id": "alpha", "app": {**MANIFEST["app"], "id": "shared-app"}}
    beta = {**MANIFEST, "id": "beta", "app": {**MANIFEST["app"], "id": "shared-app"}}
    assert publish(client, alpha).status_code == 201
    assert client.post("/api/v1/plugins/alpha/install", headers=auth()).status_code == 200
    current = client.get("/api/v1/plugins/alpha", headers=auth())
    moved = {**alpha, "version": "2.0.0", "app": {**alpha["app"], "id": "alpha-app"}}
    assert client.put(
        "/api/v1/plugins/alpha",
        headers=auth("admin", **{"If-Match": current.headers["etag"]}),
        json=moved,
    ).status_code == 200
    # The catalogue no longer owns shared-app, but Alice's accepted snapshot does.
    assert client.get("/api/v1/apps/shared-app", headers=auth()).status_code == 200
    assert publish(client, beta).json()["error"]["code"] == "APP_ID_CONFLICT"
    assert client.post(
        "/api/v1/apps",
        headers=auth("admin"),
        json={"id": "shared-app", "title": "Collision", "origin": "https://collide.example"},
    ).json()["error"]["code"] == "APP_EXISTS"
    ids = [entry["id"] for entry in client.get("/api/v1/apps", headers=auth()).json()["data"]]
    assert ids.count("shared-app") == 1
    # Dropping the snapshot releases the id.
    assert client.delete("/api/v1/plugins/alpha/install", headers=auth()).status_code == 200
    assert publish(client, beta).status_code == 201


def test_installed_widget_snapshot_does_not_follow_catalogue_edits(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert publish(client).status_code == 201
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    current = client.get("/api/v1/plugins/hello-plugin", headers=auth())
    without_widget = {**MANIFEST, "version": "2.0.0", "widget": None}
    assert client.put(
        "/api/v1/plugins/hello-plugin",
        headers=auth("admin", **{"If-Match": current.headers["etag"]}),
        json=without_widget,
    ).status_code == 200
    # The installed snapshot still declares the widget.
    widgets = client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"]
    assert [widget["plugin_id"] for widget in widgets] == ["hello-plugin"]
    item = client.get("/api/v1/plugins", headers=auth()).json()["data"][0]
    assert item["widget"] is None and item["installed_widget"] is True
    # Reinstalling accepts the new catalogue version, which has no widget.
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    assert client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"] == []
    assert client.get("/api/v1/plugins", headers=auth()).json()["data"][0]["installed_widget"] is False


def test_revoked_scopes_stop_projection_and_hide_plugin_widgets(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert publish(client).status_code == 201
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    assert any(
        entry["id"] == "hello-app" for entry in client.get("/api/v1/apps", headers=auth()).json()["data"]
    )
    scopes = settings.dev_tokens["alice"]["scope"]
    settings.dev_tokens["alice"]["scope"] = " ".join(
        item for item in scopes.split() if item != "xelibrary:read"
    )
    try:
        assert all(
            entry["id"] != "hello-app"
            for entry in client.get("/api/v1/apps", headers=auth()).json()["data"]
        )
        assert client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"] == []
        denied = client.patch(
            "/api/v1/plugins/hello-plugin/install", headers=auth(), json={"enabled": True}
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["details"] == {"missing_scopes": ["xelibrary:read"]}
    finally:
        settings.dev_tokens["alice"]["scope"] = scopes
    assert client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"] != []


def test_catalog_mutations_require_admin_and_preconditions(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    assert publish(client).status_code == 201
    assert client.put(
        "/api/v1/plugins/hello-plugin", headers=auth(), json={**MANIFEST, "version": "1.1.0"}
    ).status_code == 403
    assert client.delete("/api/v1/plugins/hello-plugin", headers=auth()).status_code == 403
    assert client.delete("/api/v1/plugins/hello-plugin", headers=auth("admin")).status_code == 412
    current = client.get("/api/v1/plugins/hello-plugin", headers=auth())
    stale = client.delete(
        "/api/v1/plugins/hello-plugin", headers=auth("admin", **{"If-Match": '"revision:99"'})
    )
    assert stale.status_code == 412
    assert client.delete(
        "/api/v1/plugins/hello-plugin",
        headers=auth("admin", **{"If-Match": current.headers["etag"]}),
    ).status_code == 200


def test_reserved_and_legacy_numeric_app_origins_are_rejected(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    reserved = {**MANIFEST, "app": {**MANIFEST["app"], "id": "xelibrary"}}
    refused = publish(client, reserved)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] in {"RESERVED_APP_ID", "VALIDATION_ERROR"}
    for origin in ("https://0177.0.0.1", "https://127.1", "https://0x7f.1"):
        body = {**MANIFEST, "app": {**MANIFEST["app"], "origin": origin}}
        response = publish(client, body)
        assert response.status_code == 422, (origin, response.text)
        assert response.json()["error"]["code"] == "INVALID_ORIGIN"


def test_first_draft_plugin_database_is_repaired_on_startup(settings, tmp_path):
    import json
    import sqlite3

    database = tmp_path / "desktop.sqlite"
    db = sqlite3.connect(database)
    db.executescript(
        """
        CREATE TABLE apps(id TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE layouts(owner TEXT PRIMARY KEY,layout TEXT);
        CREATE TABLE preferences(owner TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE app_audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,target TEXT,at REAL);
        CREATE TABLE plugins(id TEXT PRIMARY KEY,version TEXT NOT NULL,manifest TEXT NOT NULL,
          published_by TEXT NOT NULL,published_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE plugin_installs(owner TEXT NOT NULL,plugin_id TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,installed_version TEXT NOT NULL,installed_at REAL NOT NULL,
          PRIMARY KEY(owner,plugin_id));
        CREATE TABLE plugin_audit(id TEXT PRIMARY KEY,actor TEXT NOT NULL,event TEXT NOT NULL,
          plugin_id TEXT NOT NULL,at REAL NOT NULL);
        """
    )
    db.execute(
        "INSERT INTO plugins VALUES(?,?,?,?,?,?)",
        ("hello-plugin", "1.0.0", json.dumps(MANIFEST), "admin", 1.0, 1.0),
    )
    db.execute(
        "INSERT INTO plugin_installs VALUES(?,?,?,?,?)",
        ("alice", "hello-plugin", 1, "1.0.0", 1.0),
    )
    db.execute(
        "INSERT INTO plugin_installs VALUES(?,?,?,?,?)",
        ("alice", "removed-plugin", 1, "1.0.0", 1.0),
    )
    db.commit()
    db.close()

    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    item = client.get("/api/v1/plugins", headers=auth("admin")).json()["data"][0]
    assert item["id"] == "hello-plugin" and item["revision"] == 1
    assert item["app"]["id"] == "hello-app"
    assert client.get("/api/v1/apps/hello-app", headers=auth()).status_code == 200
    leaked = sqlite3.connect(database).execute(
        "SELECT plugin_id FROM plugin_installs WHERE plugin_id='removed-plugin'"
    ).fetchall()
    assert leaked == []
