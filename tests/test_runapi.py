import json

import httpx2
import pytest

from gfjproxy.app import create_app
from gfjproxy.models import JaiResult
from gfjproxy.providers.runapi import RUNAPI_BASE_URL
from gfjproxy.xuiduser import LocalUserStorage

AUTH = {"Authorization": "Bearer sk-runapi-test"}
BODY = {
    "model": "[V假流]gemini-4-argon",
    "messages": [{"role": "user", "content": "A short chat."}],
}
REPLY = {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}


@pytest.fixture
def client(monkeypatch, mocker):
    monkeypatch.delenv("GFJPROXY_ACCESS_KEYS", raising=False)
    monkeypatch.delenv("GFJPROXY_VERTEX_MODELS", raising=False)
    monkeypatch.setenv("GFJPROXY_RUNAPI_MODE", "auto")
    mocker.patch("gfjproxy.routes.proxy.storage", LocalUserStorage())
    mocker.patch("gfjproxy.routes.proxy.get_cooldown", return_value=0)
    return create_app().test_client()


@pytest.fixture
def upstream(monkeypatch):
    def install(handler):
        client = httpx2.Client(
            base_url=RUNAPI_BASE_URL,
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
        )
        monkeypatch.setattr("gfjproxy.providers.runapi.runapi_client", client)
        return client

    return install


def test_auto_routes_runapi_and_keeps_vertex_keys_local(
    client, upstream, monkeypatch, mocker
):
    monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", "sk-existing-vertex")
    vertex = mocker.patch(
        "gfjproxy.handlers.vertex_generate_content",
        return_value=JaiResult(200, "Vertex"),
    )
    requests = []

    def reply(request):
        requests.append(request)
        assert str(request.url) == RUNAPI_BASE_URL + "chat/completions"
        assert request.headers["authorization"] == AUTH["Authorization"]
        assert json.loads(request.content) == {
            **BODY,
            "stream": False,
            "temperature": 0,
        }
        return httpx2.Response(200, json=REPLY)

    with upstream(reply):
        result = client.post(
            "/", json={**BODY, "temperature": 0, "api_key": "unused"}, headers=AUTH
        )
        assert result.json == REPLY
        vertex.assert_not_called()
        result = client.post(
            "/",
            json={**BODY, "model": "gemini-2.5-flash"},
            headers={"Authorization": "Bearer sk-existing-vertex"},
        )
        assert result.status_code == 200
        vertex.assert_called_once()
        assert len(requests) == 1


def test_path_mode_requires_runapi_url_and_explicit_key(
    client, upstream, monkeypatch, mocker
):
    monkeypatch.setenv("GFJPROXY_RUNAPI_MODE", "path")
    vertex = mocker.patch(
        "gfjproxy.handlers.vertex_generate_content",
        return_value=JaiResult(200, "Vertex"),
    )
    requests = []

    def reply(request):
        requests.append(request)
        return httpx2.Response(200, json=REPLY)

    with upstream(reply):
        assert (
            client.post(
                "/", json={**BODY, "model": "gemini-2.5-flash"}, headers=AUTH
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/runapi/", json=BODY, headers={"Authorization": "Bearer vertex"}
            ).status_code
            == 401
        )
        assert not requests
        assert client.post("/runapi/", json=BODY, headers=AUTH).json == REPLY
        assert len(requests) == 1
        vertex.assert_called_once()


def test_disabled_route_and_empty_vertex_allowlist_fail_closed(
    client, upstream, monkeypatch
):
    monkeypatch.delenv("GFJPROXY_RUNAPI_MODE")
    with upstream(lambda _: pytest.fail("unexpected upstream request")):
        assert client.post("/runapi/", json=BODY, headers=AUTH).status_code == 503
        monkeypatch.setenv("GFJPROXY_RUNAPI_MODE", "auto")
        monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", "  ")
        assert client.post("/", json=BODY, headers=AUTH).status_code == 503


def test_models_and_every_503_use_current_catalog_with_same_key(client, upstream):
    calls = []
    model_names = iter(("[A]one", "[B假流]two", "[C]three"))

    def reply(request):
        assert request.headers["authorization"] == AUTH["Authorization"]
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx2.Response(
                503, json={"error": {"message": "sk-runapi-test private message"}}
            )
        assert request.headers["cache-control"] == "no-cache"
        name = next(model_names)
        return httpx2.Response(
            200,
            json={
                "data": [
                    {"id": name},
                    {"id": name},
                    {"id": "sk-runapi-test"},
                    {"id": "bad\nmodel"},
                ]
            },
        )

    with upstream(reply):
        first = client.post("/", json=BODY, headers=AUTH)
        second = client.post(
            "/",
            json={**BODY, "messages": [{"role": "user", "content": "Just say TEST"}]},
            headers=AUTH,
        )
        models = client.get("/runapi/v1/models", headers=AUTH)
        assert first.status_code == second.status_code == 503
        assert first.text.endswith("\n[A]one")
        assert second.json["error"].endswith("\n[B假流]two")
        assert "sk-runapi-test" not in first.text + second.text + models.text
        assert "private message" not in first.text + second.text
        assert [m["id"] for m in models.json["data"]] == ["[C]three"]
        assert models.headers["Cache-Control"] == "no-store"
    assert calls == [
        ("POST", "/v1/chat/completions"),
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
        ("GET", "/v1/models"),
        ("GET", "/v1/models"),
    ]


def test_directory_failure_does_not_replace_original_503(client, upstream):
    def reply(request):
        return httpx2.Response(503 if request.method == "POST" else 401)

    with upstream(reply):
        result = client.post("/runapi/", json=BODY, headers=AUTH)
        assert result.status_code == 503
        assert "Could not retrieve" in result.text


def test_redirect_does_not_send_key_to_another_host(client, upstream):
    def reply(request):
        assert request.url.host == "api.runapi.sbs"
        return httpx2.Response(302, headers={"Location": "https://other.example/"})

    with upstream(reply):
        assert client.post("/runapi/", json=BODY, headers=AUTH).status_code == 502
        assert client.get("/runapi/v1/models", headers=AUTH).status_code == 502


def test_stream_is_not_buffered_and_disconnect_releases_lock(client, upstream):
    class Events(httpx2.SyncByteStream):
        closed = False
        finished = False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            self.finished = True
            yield b"data: [DONE]\n\n"

        def close(self):
            self.closed = True

    stream = Events()

    def reply(request):
        if json.loads(request.content)["stream"]:
            return httpx2.Response(
                200,
                stream=stream,
                headers={"Content-Type": "text/event-stream; charset=utf-8"},
            )
        return httpx2.Response(200, json=REPLY)

    with upstream(reply):
        result = client.post(
            "/runapi/v1/chat/completions",
            json={**BODY, "stream": True},
            headers=AUTH,
            buffered=False,
        )
        assert result.status_code == 200
        assert result.mimetype == "text/event-stream"
        assert b"first" in next(iter(result.response))
        assert not stream.finished
        assert client.post("/runapi/", json=BODY, headers=AUTH).status_code == 403
        result.close()
        assert stream.closed
        assert client.post("/runapi/", json=BODY, headers=AUTH).json == REPLY


def test_vertex_503_has_optional_admin_model_ids(client, monkeypatch, mocker):
    monkeypatch.setenv(
        "GFJPROXY_VERTEX_MODELS", "gemini-test,invalid-model,gemini-test,gemini-other"
    )
    mocker.patch(
        "gfjproxy.handlers.vertex_generate_content",
        return_value=JaiResult(503, "Vertex unavailable"),
    )
    headers = {"Authorization": "Bearer vertex-key"}
    response = client.post("/", json={**BODY, "model": "gemini-test"}, headers=headers)
    assert response.status_code == 503
    assert response.text.endswith("\ngemini-test\ngemini-other")
    assert [
        m["id"] for m in client.get("/v1/models", headers=headers).json["data"]
    ] == ["gemini-test", "gemini-other"]
