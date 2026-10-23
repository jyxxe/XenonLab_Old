import asyncio
import copy
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab.capabilities import CapabilityRegistry, load_registry
from xeonolab.common import APIError, Identity
from xeonolab.services import Search, ToolQuery, create_app
from xeonolab.tool_adapters import ToolAdapter, ToolAdapters

from .conftest import auth


async def query_handler(request, body):
    return {"expression": body.expression}


def adapter(handler=query_handler, **kwargs):
    return ToolAdapter("xelibrary.query", "builtin.platform.tool.xelibrary-query", "Query", ToolQuery,
                       frozenset(["xelibrary:query"]), handler, **kwargs)


def actor(*scopes):
    return Identity("synthetic", set(scopes))


def registry_with(**overrides):
    manifest = copy.deepcopy(load_registry().all())
    for item in manifest["capabilities"]:
        if item["id"] == "builtin.platform.tool.xelibrary-query":
            item.update(overrides)
    return CapabilityRegistry(manifest)


def test_resolution_rechecks_enablement_permissions_version_and_trusted_tier():
    registry = ToolAdapters([adapter()])
    privileged = actor("xelibrary:query")
    assert registry.resolve("xelibrary.query", privileged)[0].name == "xelibrary.query"
    with pytest.raises(APIError) as denied:
        registry.resolve("xelibrary.query", actor())
    assert denied.value.status == 403
    registry.set_enabled("xelibrary.query", False)
    assert registry.describe(privileged) == []
    with pytest.raises(APIError) as disabled:
        asyncio.run(registry.invoke("xelibrary.query", privileged, None, ToolQuery(expression="x")))
    assert disabled.value.code == "CAPABILITY_NOT_FOUND"
    registry.set_enabled("xelibrary.query", True)
    assert len(registry.describe(privileged)) == 1
    with pytest.raises(APIError):
        registry.resolve("unregistered.plugin.tool", privileged)
    with pytest.raises(APIError):
        registry.set_enabled("unregistered.plugin.tool", True)
    for overrides, code in [
        ({"runtime": "ui"}, "CAPABILITY_INVALID"),
        ({"kind": "provider"}, "CAPABILITY_INVALID"),
        ({"permissions": []}, "CAPABILITY_INVALID"),
        ({"version": "2.0.0"}, "CAPABILITY_INCOMPATIBLE"),
    ]:
        bad = ToolAdapters([adapter()], registry_loader=lambda: registry_with(**overrides))
        with pytest.raises(APIError) as error:
            bad.resolve("xelibrary.query", privileged)
        assert error.value.code == code


def test_deadline_cancels_cooperative_handler_and_failures_do_not_poison_other_tools():
    cancelled = []

    async def slow(request, body):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    async def broken(request, body):
        raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")

    async def exercise():
        privileged = actor("xelibrary:query")
        timed = ToolAdapters([adapter(slow)], registry_loader=lambda: registry_with(timeout_seconds=1))
        with pytest.raises(APIError) as error:
            await timed.invoke("xelibrary.query", privileged, None, ToolQuery(expression="x"))
        assert error.value.code == "CAPABILITY_TIMEOUT" and cancelled == [True]
        broken_registry = ToolAdapters([adapter(broken), replace(adapter(), name="healthy", capability_id="builtin.platform.tool.xelibrary-search")])
        with pytest.raises(APIError) as error:
            await broken_registry.invoke("xelibrary.query", privileged, None, ToolQuery(expression="x"))
        assert error.value.code == "CAPABILITY_FAILED"
        assert "SYNTHETIC_PRIVATE_DETAIL" not in error.value.message
        assert await broken_registry.invoke("healthy", privileged, None, ToolQuery(expression="works")) == {"expression": "works"}
        with pytest.raises(APIError) as error:
            await broken_registry.invoke("healthy", privileged, None, Search(query="wrong schema"))
        assert error.value.status == 422
    asyncio.run(exercise())


def test_registration_rejects_duplicates_and_sync_code():
    with pytest.raises(ValueError):
        ToolAdapters([adapter(), adapter()])
    with pytest.raises(ValueError):
        ToolAdapters([adapter(lambda request, body: {})])


@pytest.mark.parametrize("enabled", [True, False])
def test_existing_tools_preserve_paths_payload_identity_request_id_and_errors(settings, monkeypatch, enabled):
    settings.rate_limit_enabled = False
    seen = []

    def upstream(request):
        seen.append(request)
        if request.url.path == "/api/v1/query":
            return httpx.Response(422, json={"error": {"code": "XL_QUERY_INVALID", "message": "query rejected"}})
        if request.url.path == "/api/v1/search/semantic":
            return httpx.Response(200, json={"data": [{"title": "result"}], "meta": {}})
        return httpx.Response(200, json={"data": {"values": {"title": "visible", "hidden": "omit"}}, "meta": {}})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(upstream)))
    app = create_app("tool-gateway", settings)
    app.state.tool_adapters_enabled = enabled
    client = TestClient(app)
    headers = {**auth(), "X-Request-ID": "33333333-3333-4333-8333-333333333333"}
    listing = client.get("/api/v1/tools", headers=headers)
    assert [item["function"]["name"] for item in listing.json()["data"]] == ["xelibrary.search", "xelibrary.query", "xelibrary.get"]
    assert listing.json()["data"][0]["function"]["parameters"] == Search.model_json_schema()
    assert client.post("/api/v1/tools/xelibrary.query", headers=headers, json={"expression": "bad"}).json()["error"]["code"] == "XL_QUERY_INVALID"
    assert client.post("/api/v1/tools/xelibrary.search", headers=headers, json={"query": "find"}).status_code == 200
    ids = {"table_id": "11111111-1111-4111-8111-111111111111", "row_id": "22222222-2222-4222-8222-222222222222"}
    got = client.post("/api/v1/tools/xelibrary.get", headers=headers, json={**ids, "fields": ["title"]})
    assert got.json()["data"]["values"] == {"title": "visible"}
    assert [request.url.path for request in seen] == ["/api/v1/query", "/api/v1/search/semantic", f"/api/v1/tables/{ids['table_id']}/rows/{ids['row_id']}"]
    assert all(request.headers["authorization"] == "Bearer alice" for request in seen)
    assert all(request.headers["x-request-id"] == "33333333-3333-4333-8333-333333333333" for request in seen)
    assert seen[0].content == b'{"expression":"bad"}'
    invalid = client.post("/api/v1/tools/xelibrary.get", headers=headers, json={**ids, "row_id": "../../escape"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_ID"
    assert len(seen) == 3
    assert client.post("/api/v1/tools/xelibrary.query", json={"expression": "x"}).status_code == 401


def test_live_http_permissions_disable_and_static_adapter_replacement(settings):
    settings.rate_limit_enabled = False
    settings.dev_tokens["reader"] = {"sub": "reader", "scope": "xelibrary:read"}
    settings.dev_tokens["query-only"] = {"sub": "query-only", "scope": "xelibrary:query"}
    app = create_app("tool-gateway", settings)
    client = TestClient(app)
    assert client.post("/api/v1/tools/xelibrary.query", headers=auth("reader"), json={"expression": "x"}).status_code == 403
    listing = client.get("/api/v1/tools", headers=auth("query-only")).json()["data"]
    assert [item["function"]["name"] for item in listing] == ["xelibrary.search", "xelibrary.query"]
    assert client.post("/api/v1/tools/xelibrary.get", headers=auth("query-only"), json={"table_id": "x", "row_id": "y"}).status_code == 403
    app.state.tool_adapters = ToolAdapters([adapter()])
    assert client.post("/api/v1/tools/xelibrary.query", headers=auth(), json={"expression": "x"}).json() == {"expression": "x"}
    app.state.tool_adapters.set_enabled("xelibrary.query", False)
    assert client.post("/api/v1/tools/xelibrary.query", headers=auth(), json={"expression": "x"}).status_code == 404
    assert client.get("/api/v1/tools", headers=auth()).json()["data"] == []
    app.state.tool_adapters.set_enabled("xelibrary.query", True)

    async def replacement(request, body):
        return {"replacement": body.expression}

    app.state.tool_adapters = ToolAdapters([adapter(replacement)])
    assert client.post("/api/v1/tools/xelibrary.query", headers=auth(), json={"expression": "same caller"}).json() == {"replacement": "same caller"}


def test_adapter_flag_is_explicit_and_validated(settings, monkeypatch):
    monkeypatch.setenv("TOOL_CAPABILITY_ADAPTERS", "0")
    assert create_app("tool-gateway", settings).state.tool_adapters_enabled is False
    monkeypatch.setenv("TOOL_CAPABILITY_ADAPTERS", "unexpected")
    with pytest.raises(ValueError, match="TOOL_CAPABILITY_ADAPTERS"):
        create_app("tool-gateway", settings)


@pytest.mark.parametrize("failure,code", [("timeout", "CAPABILITY_TIMEOUT"), ("protocol", "CAPABILITY_FAILED")])
def test_http_adapter_errors_do_not_expose_upstream_details(settings, monkeypatch, failure, code):
    def upstream(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_DETAIL", request=request)
        return httpx.Response(200, content=b"SYNTHETIC_PRIVATE_DETAIL")

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(upstream)))
    client = TestClient(create_app("tool-gateway", settings))
    response = client.post("/api/v1/tools/xelibrary.query", headers=auth(), json={"expression": "x"})
    assert response.status_code == 503 and response.json()["error"]["code"] == code
    assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text


def test_execution_dependencies_require_live_authorized_static_bindings():
    manifest = registry_with(dependencies=["builtin.platform.tool.xelibrary-search@1.0.0"]).all()
    for item in manifest["capabilities"]:
        if item["id"] == "builtin.platform.tool.xelibrary-search":
            item["dependencies"] = ["builtin.platform.tool.xelibrary-get@1.0.0"]
    registry = CapabilityRegistry(manifest)
    root = adapter()
    dependency = replace(adapter(), name="dependency", capability_id="builtin.platform.tool.xelibrary-get",
                         permissions=frozenset(["xelibrary:read"]))
    bridge = replace(adapter(), name="bridge", capability_id="builtin.platform.tool.xelibrary-search")
    tools = ToolAdapters([root, bridge, dependency], registry_loader=lambda: registry)
    with pytest.raises(APIError) as error:
        tools.resolve(root.name, actor("xelibrary:query"))
    assert error.value.status == 403
    tools.set_enabled("dependency", False)
    with pytest.raises(APIError) as error:
        tools.resolve(root.name, actor("xelibrary:query", "xelibrary:read"))
    assert error.value.status == 404
    tools.set_enabled("dependency", True)
    assert tools.resolve(root.name, actor("xelibrary:query", "xelibrary:read"))[0] == root
    unbound = ToolAdapters([root], registry_loader=lambda: registry)
    with pytest.raises(APIError) as error:
        unbound.resolve(root.name, actor("xelibrary:query", "xelibrary:read"))
    assert error.value.code == "CAPABILITY_DEPENDENCY_UNAVAILABLE"


class AuthorityActor(Identity):
    """Synthetic verified core snapshot, not a plugin/user-supplied object."""
    def __init__(self, disabled=()):
        super().__init__("synthetic", {"xelibrary:query", "xelibrary:read"})
        self.lifecycle_revision = 7
        self.disabled = set(disabled)

    def require(self, scope):
        assert scope in {'xelibrary:query', 'xelibrary:read'}

    def require_capability(self, capability_id):
        if capability_id in self.disabled:
            raise APIError(404, 'CAPABILITY_NOT_FOUND', 'no such capability')


def test_authority_denial_intersects_local_enablement_and_description():
    adapters = ToolAdapters([adapter()], lifecycle_required=True)
    capability = adapter().capability_id
    denied = AuthorityActor([capability])
    assert adapters.describe(denied) == []
    assert not adapters.lifecycle_visible('xelibrary.query', denied)
    adapters.set_enabled('xelibrary.query', True)
    with pytest.raises(APIError) as error:
        asyncio.run(adapters.invoke('xelibrary.query', denied, None, ToolQuery(expression='test')))
    assert error.value.code == 'CAPABILITY_NOT_FOUND'
    allowed = AuthorityActor()
    assert len(adapters.describe(allowed)) == 1
    adapters.set_enabled('xelibrary.query', False)
    assert adapters.describe(allowed) == []


def test_strict_lifecycle_rejects_old_or_unverified_identity_objects():
    strict = ToolAdapters([adapter()], lifecycle_required=True)
    for identity in (actor('xelibrary:query'), AuthorityActor()):
        if isinstance(identity, AuthorityActor):
            identity.lifecycle_revision = None
        with pytest.raises(APIError) as error:
            strict.resolve('xelibrary.query', identity)
        assert error.value.code == 'CAPABILITY_LIFECYCLE_UNAVAILABLE'
    assert ToolAdapters([adapter()], lifecycle_required=False).resolve('xelibrary.query', actor('xelibrary:query'))


def test_dependency_resolution_checks_authority_state():
    manifest = copy.deepcopy(load_registry().all())
    root = next(c for c in manifest['capabilities'] if c['id'] == adapter().capability_id)
    root['dependencies'] = ['builtin.platform.tool.xelibrary-search@^1.0.0']
    registry = CapabilityRegistry(manifest)
    adapters = ToolAdapters([
        adapter(), replace(adapter(), name='dependency', capability_id='builtin.platform.tool.xelibrary-search'),
    ], registry_loader=lambda: registry, lifecycle_required=True)
    with pytest.raises(APIError) as error:
        adapters.resolve('xelibrary.query', AuthorityActor(['builtin.platform.tool.xelibrary-search']))
    assert error.value.code == 'CAPABILITY_NOT_FOUND'


@pytest.mark.parametrize('flag', ['0', '1'])
def test_http_authority_denial_is_not_bypassed_by_adapter_flag(settings, monkeypatch, flag):
    monkeypatch.setenv('TOOL_CAPABILITY_ADAPTERS', flag)
    app = create_app('tool-gateway', settings)
    snapshot = AuthorityActor([adapter().capability_id])
    app.state.authorization_resolver = lambda token: snapshot
    called = []

    async def request(*args, **kwargs):
        called.append(True)
        raise AssertionError('disabled capability must not forward')

    monkeypatch.setattr(httpx.AsyncClient, 'request', request)
    client = TestClient(app)
    denied = client.post('/api/v1/tools/xelibrary.query', headers=auth(), json={'expression': 'test'})
    assert denied.status_code == 404
    descriptions = client.get('/api/v1/tools', headers=auth()).json()['data']
    assert 'xelibrary.query' not in {t['function']['name'] for t in descriptions}
    assert not called
