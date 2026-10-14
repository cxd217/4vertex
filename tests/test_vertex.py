import json

import httpx2
import pytest
from google.auth.exceptions import RefreshError

from gfjproxy import vertex_auth
from gfjproxy.models import JaiMessage, JaiRequest
from gfjproxy.providers.vertex import vertex_generate_content
from gfjproxy.xuiduser import XUID


@pytest.fixture
def auth_environment(monkeypatch):
    for key in (
        "PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CREDENTIALS",
        "VERTEX_LOCATION",
        "GOOGLE_CLOUD_LOCATION",
    ):
        monkeypatch.delenv(key, raising=False)
    vertex_auth._load_credentials.cache_clear()
    yield
    vertex_auth._load_credentials.cache_clear()


@pytest.mark.parametrize(
    "location,host",
    [
        ("global", "aiplatform.googleapis.com"),
        ("us-central1", "us-central1-aiplatform.googleapis.com"),
        ("eu", "aiplatform.eu.rep.googleapis.com"),
    ],
)
def test_server_credentials_and_endpoint(
    mocker, monkeypatch, auth_environment, location, host
):
    info = {
        "type": "service_account",
        "project_id": "credential-project",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    monkeypatch.setenv("GOOGLE_CREDENTIALS", json.dumps(info))
    monkeypatch.setenv("PROJECT_ID", "chosen-project")
    monkeypatch.setenv("VERTEX_LOCATION", location)
    credential = mocker.Mock()

    def authorize(auth_request, method, url, headers):
        headers["authorization"] = "Bearer server-token"

    credential.before_request.side_effect = authorize
    loader = mocker.patch(
        "gfjproxy.vertex_auth.service_account.Credentials.from_service_account_info",
        return_value=credential,
    )
    url, headers = vertex_auth.vertex_request_auth("gemini-2.5-flash")
    assert url == (
        f"https://{host}/v1/projects/chosen-project/locations/{location}"
        "/publishers/google/models/gemini-2.5-flash:generateContent"
    )
    assert headers["authorization"] == "Bearer server-token"
    assert "x-goog-api-key" not in headers
    loader.assert_called_once_with(info, scopes=vertex_auth.SCOPES)
    # Credentials stay cached; before_request runs each time to refresh expired tokens.
    vertex_auth.vertex_request_auth("gemini-2.5-pro")
    assert loader.call_count == 1
    assert credential.before_request.call_count == 2


def test_adc_and_default_global(mocker, auth_environment):
    credential = mocker.Mock()
    loader = mocker.patch(
        "gfjproxy.vertex_auth.google.auth.default",
        return_value=(credential, "adc-project"),
    )
    url, _ = vertex_auth.vertex_request_auth("gemini-2.5-flash")
    assert "/projects/adc-project/locations/global/" in url
    assert url.startswith("https://aiplatform.googleapis.com/")
    loader.assert_called_once_with(scopes=vertex_auth.SCOPES)


@pytest.mark.parametrize(
    "value",
    [
        "bad-json",
        "[]",
        '{"type":"authorized_user"}',
        '{"type":"service_account","token_uri":"https://example.com/token"}',
    ],
)
def test_invalid_credentials_do_not_echo_secrets(monkeypatch, auth_environment, value):
    monkeypatch.setenv("GOOGLE_CREDENTIALS", value)
    with pytest.raises(vertex_auth.VertexConfigurationError) as error:
        vertex_auth.vertex_request_auth("gemini-2.5-flash")
    assert value not in str(error.value)


def test_payload_system_search_usage(mocker):
    post = mocker.patch("gfjproxy.providers.vertex.http_client.post")
    post.return_value = httpx2.Response(
        200,
        request=httpx2.Request("POST", "https://example.com"),
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "hidden", "thought": True},
                            {"text": "Hello"},
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 4,
                "thoughtsTokenCount": 2,
                "totalTokenCount": 9,
            },
        },
    )
    result = vertex_generate_content(
        XUID("u", "s"),
        "gemini-2.5-flash",
        [
            JaiMessage(role="system", content="Follow the character."),
            JaiMessage(role="user", content="Hi"),
            JaiMessage(role="assistant", content="Prefill"),
        ],
        {"search": True},
    )
    body = post.call_args.kwargs["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "Follow the character."}]}
    assert [entry["role"] for entry in body["contents"]] == ["user", "model"]
    assert body["tools"] == [{"googleSearch": {}}]
    assert len(body["safetySettings"]) == 4
    assert result.text == "Hello"
    assert result.metadata.token_usage.total_tokens == 9


@pytest.mark.parametrize(
    "error,status",
    [
        (vertex_auth.VertexConfigurationError("Missing project"), 503),
        (RefreshError("secret-private-key"), 502),
    ],
)
def test_auth_failure_is_server_error(mocker, error, status):
    mocker.patch("gfjproxy.providers.vertex.vertex_request_auth", side_effect=error)
    post = mocker.patch("gfjproxy.providers.vertex.http_client.post")
    result = vertex_generate_content(XUID("u", "s"), "gemini-2.5-flash", [JaiMessage()])
    assert result.status == status
    assert "secret-private-key" not in result.error
    assert result.metadata.api_key_valid
    post.assert_not_called()


def test_non_json_error(mocker):
    mocker.patch(
        "gfjproxy.providers.vertex.http_client.post",
        return_value=httpx2.Response(
            503,
            text="Upstream unavailable",
            request=httpx2.Request("POST", "https://example.com"),
        ),
    )
    result = vertex_generate_content(XUID("u", "s"), "gemini-2.5-flash", [JaiMessage()])
    assert result.status == 503
    assert "Upstream unavailable" in result.error


@pytest.mark.parametrize(
    "model",
    [
        "gemini-2.5-flash",
        "google/gemini-2.5-flash",
        "vertex/gemini-2.5-flash",
        "vertexai/gemini-2.5-flash",
    ],
)
def test_vertex_model_aliases(model):
    assert JaiRequest.parse({"model": model}).models == {"google": "gemini-2.5-flash"}


@pytest.mark.parametrize(
    "model",
    [
        "deepseek/deepseek-chat",
        "openrouter/google/gemini-2.5-flash",
        "gemini-2.5-pro,gemini-2.5-flash",
        "google/../../other",
    ],
)
def test_other_providers_and_invalid_models_rejected(model):
    assert "unknown" in JaiRequest.parse({"model": model}).models
