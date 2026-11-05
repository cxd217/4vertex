import json

import httpx2
import pytest

from gfjproxy._globals import BANNER
from gfjproxy.app import create_app
from gfjproxy.xuiduser import LocalUserStorage

AUTH = {"Authorization": "Bearer vertex-test"}
BODY = {
    "model": "gemini-2.5-flash",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": True,
}
URL = "https://aiplatform.googleapis.com/v1/projects/test/locations/global/publishers/google/models/"


def event(*parts, **candidate):
    return {"candidates": [{"content": {"parts": list(parts)}, **candidate}]}


def sse(value):
    return f"data: {json.dumps(value, ensure_ascii=False)}\r\n\r\n".encode()


def read_events(reply):
    frames = [
        json.loads(line[6:])
        for line in reply.get_data(as_text=True).splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    return [frame["choices"][0] for frame in frames]


@pytest.fixture
def client(monkeypatch, mocker):
    for key in (
        "GFJPROXY_ACCESS_KEYS",
        "GFJPROXY_RUNAPI_MODE",
        "GFJPROXY_VERTEX_MODELS",
    ):
        monkeypatch.delenv(key, raising=False)
    storage = LocalUserStorage()
    mocker.patch("gfjproxy.routes.proxy.storage", storage)
    mocker.patch("gfjproxy.routes.proxy.get_cooldown", return_value=0)
    return create_app().test_client(), storage


@pytest.fixture
def upstream(monkeypatch):
    def auth(model, *, stream=False):
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{URL}{model}:{method}", {"Authorization": "Bearer vertex-upstream"}

    monkeypatch.setattr("gfjproxy.providers.vertex_stream.vertex_request_auth", auth)
    monkeypatch.setattr("gfjproxy.providers.vertex.vertex_request_auth", auth)

    def install(handler):
        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        monkeypatch.setattr("gfjproxy.providers.vertex_stream.http_client", client)
        monkeypatch.setattr("gfjproxy.providers.vertex.http_client", client)
        return client

    return install


def test_vertex_returns_first_text_before_final_event_and_then_appends_extras(
    client, upstream
):
    browser, storage = client
    storage.announcement = "Service notice"

    class Events(httpx2.SyncByteStream):
        reached_second = False
        closed = False

        def __iter__(self):
            thought = sse(event({"text": "private thoughts", "thought": True}))
            first = sse(event({"text": "  你好 "}))
            split = first.index("你".encode()) + 1
            yield b": ping\r\n\r\n" + thought + first[:split]
            yield first[split:]
            self.reached_second = True
            yield sse(
                event(
                    {"text": "world  "},
                    finishReason="MAX_TOKENS",
                    groundingMetadata={
                        "webSearchQueries": ["one search"],
                        "groundingChunks": [
                            {"web": {"uri": "https://example.com/source"}}
                        ],
                    },
                )
            )
            yield sse(
                {"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}
            )

        def close(self):
            self.closed = True

    events = Events()

    def respond(request):
        assert request.url.path.endswith(":streamGenerateContent")
        assert request.url.params["alt"] == "sse"
        assert request.headers["authorization"] == "Bearer vertex-upstream"
        assert request.headers["accept"] == "text/event-stream"
        assert json.loads(request.content)["contents"] == [
            {"role": "user", "parts": [{"text": "Hello"}]}
        ]
        return httpx2.Response(
            200, stream=events, headers={"Content-Type": "text/event-stream"}
        )

    with (
        upstream(respond),
        browser.post("/", json=BODY, headers=AUTH, buffered=False) as reply,
    ):
        assert reply.status_code == 200
        assert reply.mimetype == "text/event-stream"
        assert reply.headers["X-Accel-Buffering"] == "no"
        assert not events.reached_second
        frames = read_events(reply)
        assert frames[0]["delta"]["content"] == "你好"
        assert frames[1]["delta"]["content"] == " world"
        text = "".join(frame["delta"].get("content", "") for frame in frames)
        assert (
            text.index("one search") < text.index(BANNER) < text.index("Service notice")
        )
        assert "https://example.com/source" in text
        assert "private thoughts" not in text
        assert frames[-1]["finish_reason"] == "length"
        assert reply.get_data(as_text=True).endswith("data: [DONE]\n\n")
    assert events.closed


def test_disconnecting_closes_vertex_stream_and_unlocks_user(client, upstream):
    browser, _ = client

    class Events(httpx2.SyncByteStream):
        closed = False

        def __iter__(self):
            yield sse(event({"text": "first"}))
            pytest.fail("read further after the client disconnected")

        def close(self):
            self.closed = True

    events = Events()

    def respond(request):
        if request.url.path.endswith(":streamGenerateContent"):
            return httpx2.Response(
                200, stream=events, headers={"Content-Type": "text/event-stream"}
            )
        return httpx2.Response(200, json=event({"text": "Recovered"}))

    with upstream(respond):
        result = browser.post("/quiet/", json=BODY, headers=AUTH, buffered=False)
        assert b"first" in next(iter(result.response))
        assert browser.post("/quiet/", json=BODY, headers=AUTH).status_code == 403
        result.close()
        assert events.closed
        recovered = browser.post(
            "/quiet/", json={**BODY, "stream": False}, headers=AUTH
        )
        assert recovered.json["choices"][0]["message"]["content"] == "Recovered"


def test_vertex_stream_keeps_http_error_before_first_text(
    client, upstream, monkeypatch
):
    browser, _ = client
    monkeypatch.setenv("GFJPROXY_VERTEX_MODELS", "gemini-test,gemini-other")
    with upstream(
        lambda _: httpx2.Response(
            503, json={"error": {"code": 503, "message": "Unavailable"}}
        )
    ):
        reply = browser.post("/", json=BODY, headers=AUTH)
        assert reply.status_code == 503
        assert reply.text.endswith("\ngemini-test\ngemini-other")
        assert reply.mimetype == "text/plain"


def test_vertex_truncated_stream_does_not_show_a_success_banner(client, upstream):
    browser, _ = client
    with (
        upstream(
            lambda _: httpx2.Response(
                200,
                content=sse(event({"text": "partial"})),
                headers={"Content-Type": "text/event-stream"},
            )
        ),
        browser.post("/", json=BODY, headers=AUTH) as reply,
    ):
        text = "".join(
            frame["delta"].get("content", "") for frame in read_events(reply)
        )
        assert text.startswith("partial")
        assert "ended the stream before completing the reply" in text
        assert BANNER not in text


def test_blocked_before_text_returns_an_error(client, upstream):
    browser, _ = client
    with upstream(
        lambda _: httpx2.Response(
            200,
            content=sse({"promptFeedback": {"blockReason": "SAFETY"}}),
            headers={"Content-Type": "text/event-stream"},
        )
    ):
        reply = browser.post("/quiet/", json=BODY, headers=AUTH)
        assert reply.status_code == 502
        assert "Response blocked/empty due to SAFETY" in reply.text


@pytest.mark.parametrize(
    ("prompt", "generated", "expected"),
    [
        (
            "//think this Hello",
            "<think>Private</think><response>Public</response>",
            "Public",
        ),
        (
            "//prefill_mode 2 //prefill this Hello",
            "<starter>Artifact</starter>Public",
            "Public",
        ),
        ("Just say TEST", "Some other text", "TEST"),
    ],
)
def test_proxy_test_and_output_cleanup_still_buffer(
    client, upstream, prompt, generated, expected
):
    browser, _ = client

    def respond(request):
        assert request.url.path.endswith(":generateContent")
        return httpx2.Response(200, json=event({"text": generated}))

    with upstream(respond):
        reply = browser.post(
            "/quiet/",
            json={**BODY, "messages": [{"role": "user", "content": prompt}]},
            headers=AUTH,
        )
        assert reply.status_code == 200
        assert reply.mimetype == "text/event-stream"
        text = "".join(
            frame["delta"].get("content", "") for frame in read_events(reply)
        )
        assert text.endswith(expected)
        assert "<think>" not in text and "<starter>" not in text
