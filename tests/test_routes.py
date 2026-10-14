import pytest

from gfjproxy.app import create_app
from gfjproxy.xuiduser import LocalUserStorage


@pytest.fixture
def client(mocker, monkeypatch):
    monkeypatch.delenv("GFJPROXY_ACCESS_KEYS", raising=False)
    mocker.patch("gfjproxy.routes.proxy.storage", LocalUserStorage())
    mocker.patch("gfjproxy.routes.proxy.get_cooldown", return_value=0)
    return create_app().test_client()


BODY = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}


@pytest.mark.parametrize(
    "path", ["/", "/chat/completions", "/quiet/", "/quiet/chat/completions"]
)
def test_routes_preserve_user_identity(client, mocker, path):
    users = []

    def handler(user, jai_req, response):
        users.append(repr(user.xuid))
        return response.add_message("Hello")

    mocker.patch("gfjproxy.routes.proxy.handle_chat_message", side_effect=handler)
    for token in ("existing-key", "existing-key,ignored-second-key"):
        reply = client.post(
            path, json=BODY, headers={"Authorization": f"Bearer {token}"}
        )
        assert reply.status_code == 200
        assert reply.json["choices"][0]["message"]["content"] == "Hello"
    assert users[0] == users[1]


@pytest.mark.parametrize("token", [None, "Bearer", "Bearer , ,", "Basic key"])
def test_empty_authorization(client, token):
    headers = {"Authorization": token} if token is not None else {}
    assert client.post("/", json=BODY, headers=headers).status_code == 401


def test_optional_access_restriction(client, mocker, monkeypatch):
    monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", "key-one,key-two")
    handler = mocker.patch(
        "gfjproxy.routes.proxy.handle_chat_message",
        side_effect=lambda u, r, response: response.add_message("Hello"),
    )
    assert (
        client.post(
            "/", json=BODY, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    handler.assert_not_called()
    assert (
        client.post(
            "/", json=BODY, headers={"Authorization": "Bearer key-two"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "body",
    [
        {"messages": [{"role": "user", "content": "Hello"}]},
        {"model": "gemini-2.5-flash"},
        {"model": "deepseek/deepseek-chat", "messages": BODY["messages"]},
        {"model": 3, "messages": BODY["messages"]},
        {"model": "gemini-2.5-flash", "messages": "bad"},
    ],
)
def test_invalid_requests_do_not_call_vertex(client, mocker, body):
    handler = mocker.patch("gfjproxy.routes.proxy.handle_chat_message")
    assert (
        client.post(
            "/", json=body, headers={"Authorization": "Bearer user"}
        ).status_code
        == 400
    )
    handler.assert_not_called()


def test_lock_released_on_handler_error(client, mocker):
    handler = mocker.patch(
        "gfjproxy.routes.proxy.handle_chat_message", side_effect=RuntimeError("test")
    )
    assert (
        client.post(
            "/", json=BODY, headers={"Authorization": "Bearer user"}
        ).status_code
        == 500
    )
    handler.side_effect = lambda u, r, response: response.add_message("Recovered")
    assert (
        client.post(
            "/", json=BODY, headers={"Authorization": "Bearer user"}
        ).status_code
        == 200
    )


def test_sse_response(client, mocker):
    mocker.patch(
        "gfjproxy.routes.proxy.handle_chat_message",
        side_effect=lambda u, r, response: response.add_message("Hello"),
    )
    reply = client.post(
        "/", json={**BODY, "stream": True}, headers={"Authorization": "Bearer user"}
    )
    assert reply.status_code == 200
    assert reply.mimetype == "text/event-stream"
    assert "[DONE]" in reply.text


def test_removed_keyring_and_public_pages(client):
    assert client.get("/keyring").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/stats").status_code == 200


def test_legacy_app_entrypoint():
    import gfjproxy.app as module

    assert module.app is module.application


@pytest.mark.parametrize("configured", ["", "   ", ", ,"])
def test_empty_whitelist_does_not_allow_everyone(
    client, mocker, monkeypatch, configured
):
    monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", configured)
    handler = mocker.patch("gfjproxy.routes.proxy.handle_chat_message")
    reply = client.post("/", json=BODY, headers={"Authorization": "Bearer user"})
    assert reply.status_code == 503
    assert "GFJPROXY_ACCESS_KEYS" in reply.json["error"]
    handler.assert_not_called()


def test_unauthorized_input_is_rejected_before_message_parsing(
    client, mocker, monkeypatch
):
    monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", "allowed-token")
    parser = mocker.patch("gfjproxy.routes.proxy.parse_valid_request")
    vertex = mocker.patch("gfjproxy.handlers.vertex_generate_content")
    reply = client.post(
        "/",
        json={"messages": "private-chat-content"},
        headers={"Authorization": "Bearer rejected-token"},
    )
    assert reply.status_code == 401
    assert "rejected-token" not in reply.text
    assert "private-chat-content" not in reply.text
    parser.assert_not_called()
    vertex.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "stream": "false"},
        {**BODY, "temperature": "hot"},
        {**BODY, "temperature": True},
        {**BODY, "temperature": float("nan")},
        {**BODY, "top_p": float("inf")},
        {**BODY, "top_k": -1},
        {**BODY, "max_tokens": 1.5},
        {**BODY, "frequency_penalty": 2.0},
        {**BODY, "messages": []},
        {**BODY, "messages": ['{"role":"user","content":"hidden"}']},
        {**BODY, "messages": [{"role": "user"}]},
        {**BODY, "messages": [{"role": "tool", "content": "hidden"}]},
        {**BODY, "messages": [{"role": "user", "content": []}]},
    ],
)
def test_illegal_payload_never_reaches_vertex(client, mocker, body):
    vertex = mocker.patch("gfjproxy.handlers.vertex_generate_content")
    reply = client.post("/", json=body, headers={"Authorization": "Bearer user"})
    assert reply.status_code == 400
    assert "hidden" not in reply.text
    vertex.assert_not_called()


def test_janitor_proxy_test_reports_validation_errors(client, mocker):
    vertex = mocker.patch("gfjproxy.handlers.vertex_generate_content")
    body = {
        "model": "gemini-2.5-flash",
        "temperature": "invalid",
        "messages": [{"role": "user", "content": "Just say TEST"}],
    }
    reply = client.post("/", json=body, headers={"Authorization": "Bearer user"})
    assert reply.status_code == 400
    assert "temperature" in reply.json["error"]
    vertex.assert_not_called()


def test_valid_janitor_proxy_test_and_stream(client, mocker, monkeypatch):
    monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", "allowed-token")
    handler = mocker.patch(
        "gfjproxy.routes.proxy.handle_proxy_test",
        side_effect=lambda u, r, response: response.add_message("TEST"),
    )
    body = {
        "model": "vertex/gemini-2.5-flash",
        "temperature": 0,
        "max_tokens": 10,
        "stream": True,
        "messages": [{"role": "user", "content": "Just say TEST"}],
    }
    reply = client.post(
        "/", json=body, headers={"Authorization": "Bearer allowed-token"}
    )
    assert reply.status_code == 200
    assert reply.mimetype == "text/event-stream"
    assert "TEST" in reply.text and "[DONE]" in reply.text
    assert handler.call_args.args[1].models == {"google": "gemini-2.5-flash"}
