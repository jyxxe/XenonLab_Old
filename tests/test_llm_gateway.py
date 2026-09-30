from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth


def test_model_alias_maps_only_on_server(settings, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "unit-test-provider-key")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "~deepseek/deepseek-flash-latest")

    class Result:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json):
            assert url == "https://api.jyxxe.com/v1/chat/completions"
            assert headers["Authorization"] == "Bearer unit-test-provider-key"
            assert json["model"] == "~deepseek/deepseek-flash-latest"
            return Result()

    monkeypatch.setattr("xeonolab.services.httpx.AsyncClient", Provider)
    client = TestClient(create_app("llm-gateway", settings))
    result = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert result.status_code == 200
    assert (
        client.post("/v1/chat/completions", headers=auth(), json={"model": "arbitrary-model"}).status_code
        == 422
    )
    assert client.post("/v1/chat/completions", headers=auth(), json=[]).status_code == 422
