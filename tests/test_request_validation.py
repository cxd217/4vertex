import pytest

from gfjproxy.request_validation import (
    ProxyAccessError,
    RequestValidationError,
    authorize_proxy_request,
    parse_valid_request,
)

BODY = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", -0.01),
        ("temperature", 2.01),
        ("top_p", -0.01),
        ("top_p", 1.01),
        ("frequency_penalty", -2.01),
        ("frequency_penalty", 2.0),
        ("repetition_penalty", -2.01),
        ("repetition_penalty", 2.0),
        ("max_tokens", -1),
        ("max_tokens", True),
        ("top_k", 0.5),
        ("top_k", False),
        ("temperature", []),
        ("frequency_penalty", {}),
        ("max_tokens", 10**400),
        ("stream", 1),
        ("stream", None),
    ],
)
def test_invalid_parameter(field, value):
    with pytest.raises(RequestValidationError, match=field):
        parse_valid_request({**BODY, field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", 0),
        ("temperature", 2),
        ("top_p", 0),
        ("top_p", 1),
        ("frequency_penalty", -2),
        ("frequency_penalty", 1.99),
        ("repetition_penalty", -2),
        ("repetition_penalty", 1.99),
        ("max_tokens", 0),
        ("top_k", 0),
        ("temperature", None),
        ("stream", False),
    ],
)
def test_valid_boundaries_and_defaults(field, value):
    assert parse_valid_request({**BODY, field: value}).models["google"] == BODY["model"]


def test_empty_assistant_prefill_and_extra_client_fields():
    body = {
        **BODY,
        "messages": [*BODY["messages"], {"role": "assistant", "content": ""}],
        "stop": ["unused"],
        "client_metadata": {"source": "janitor"},
    }
    assert len(parse_valid_request(body).messages) == 2


def test_authorized_tokens_are_case_sensitive_and_preserve_first_key(monkeypatch):
    monkeypatch.setenv("GFJPROXY_ACCESS_KEYS", "private-token,second-token")
    assert (
        authorize_proxy_request("Bearer private-token,ignored-token") == "private-token"
    )
    assert authorize_proxy_request("bearer second-token") == "second-token"
    with pytest.raises(ProxyAccessError):
        authorize_proxy_request("Bearer PRIVATE-TOKEN")
