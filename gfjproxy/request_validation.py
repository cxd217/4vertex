"""Validate access credentials and text chat requests before forwarding them."""

from hmac import compare_digest
from math import isfinite
from os import environ
from re import fullmatch

from .models import JaiRequest
from .utils import comma_split


class ProxyAccessError(ValueError):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


class RequestValidationError(ValueError):
    """Invalid client input, with a message that never includes request content."""


def _bearer_value(authorization: str) -> str:
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ProxyAccessError("Unauthorized. Proxy access token required.")
    return parts[1].strip()


def _matches_access_key(token: str, allowed: list[str]) -> bool:
    return any(compare_digest(token.encode(), key.encode()) for key in allowed)


def authorize_proxy_request(authorization: str) -> str:
    tokens = comma_split(_bearer_value(authorization))
    if not tokens:
        raise ProxyAccessError("Unauthorized. Proxy access token required.")
    # Preserve the XUID used by old comma-separated JanitorAI configurations.
    token = tokens[0]
    configured_keys = environ.get("GFJPROXY_ACCESS_KEYS")
    if configured_keys is not None:
        allowed_tokens = comma_split(configured_keys)
        if not allowed_tokens:
            raise ProxyAccessError(
                "Proxy access is not configured. Ask the owner to set GFJPROXY_ACCESS_KEYS or remove the empty variable.",
                503,
            )
        if not _matches_access_key(token, allowed_tokens):
            raise ProxyAccessError("Unauthorized. Invalid proxy access token.")
    return token


def select_runapi_key(authorization: str, *, explicit: bool) -> str | None:
    mode = environ.get("GFJPROXY_RUNAPI_MODE", "").strip().lower()
    if explicit and mode not in ("path", "auto"):
        raise ProxyAccessError(
            "RunAPI is disabled. Ask the proxy owner to enable it.", 503
        )
    if not explicit and mode != "auto":
        return None
    key = _bearer_value(authorization)
    if not fullmatch(r"sk-[A-Za-z0-9_-]{1,256}", key):
        if explicit:
            raise ProxyAccessError("Enter a single RunAPI API key starting with sk-.")
        return None
    configured_keys = environ.get("GFJPROXY_ACCESS_KEYS")
    if configured_keys is not None:
        allowed = comma_split(configured_keys)
        if _matches_access_key(key, allowed):
            if explicit:
                raise ProxyAccessError(
                    "This key is configured for Vertex. Enter a RunAPI key."
                )
            return None
        if not explicit and not allowed:
            return None  # Preserve the shared endpoint's fail-closed behavior.
    return key


def is_model_id(value: object) -> bool:
    # RunAPI aliases can contain brackets, non-ASCII text, and mixed case.
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and value == value.strip()
        and value.isprintable()
        and not any(c in value for c in ",<>")
    )


def configured_vertex_models() -> list[str]:
    return list(
        dict.fromkeys(
            model
            for model in comma_split(environ.get("GFJPROXY_VERTEX_MODELS", ""))
            if fullmatch(r"gemini-[a-z0-9][a-z0-9._-]*", model)
        )
    )


def _check_number(
    data: dict,
    field: str,
    minimum: float,
    maximum: float | None = None,
    *,
    integer: bool = False,
    exclusive_maximum: bool = False,
):
    value = data.get(field)
    # Existing clients can omit optional parameters or send null for defaults.
    if value is None:
        return
    if type(value) not in ((int,) if integer else (int, float)):
        kind = "an integer" if integer else "a number"
        raise RequestValidationError(f"{field} must be {kind}.")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise RequestValidationError(f"{field} must be a finite number.")
    if value < minimum or (
        maximum is not None
        and (value >= maximum if exclusive_maximum else value > maximum)
    ):
        limit = f">= {minimum:g}"
        if maximum is not None:
            limit += f" and {'<' if exclusive_maximum else '<='} {maximum:g}"
        raise RequestValidationError(f"{field} must be {limit}.")


def _validate_chat_fields(data: dict):
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RequestValidationError("messages must be a non-empty list.")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise RequestValidationError(f"messages[{index}] must be an object.")
        if message.get("role") not in ("system", "user", "assistant"):
            raise RequestValidationError(
                f"messages[{index}].role must be system, user or assistant."
            )
        if not isinstance(message.get("content"), str):
            raise RequestValidationError(f"messages[{index}].content must be text.")
    if "stream" in data and not isinstance(data["stream"], bool):
        raise RequestValidationError("stream must be true or false.")
    _check_number(data, "max_tokens", 0, integer=True)
    _check_number(data, "top_k", 0, integer=True)
    _check_number(data, "temperature", 0, 2)
    _check_number(data, "top_p", 0, 1)
    for field in ("frequency_penalty", "repetition_penalty"):
        _check_number(data, field, -2, 2, exclusive_maximum=True)


def parse_runapi_request(data: dict) -> dict:
    model = data.get("model")
    if not is_model_id(model):
        raise RequestValidationError("model must be a single RunAPI model ID.")
    _validate_chat_fields(data)
    _check_number(data, "max_completion_tokens", 0, integer=True)
    _check_number(data, "presence_penalty", -2, 2)
    payload = {
        "model": model,
        "messages": [
            {"role": message["role"], "content": message["content"]}
            for message in data["messages"]
        ],
        "stream": data.get("stream", False),
    }
    for field in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if data.get(field) is not None:
            payload[field] = data[field]
    for field in ("max_completion_tokens", "max_tokens"):
        if data.get(field):
            payload[field] = data[field]
            break
    return payload


def parse_valid_request(data: dict) -> JaiRequest:
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        raise RequestValidationError("model must be a non-empty Gemini model name.")
    _validate_chat_fields(data)
    jai_req = JaiRequest.parse(data)
    if jai_req.models.get("unknown") or not jai_req.models.get("google"):
        raise RequestValidationError(
            "Use one Vertex AI Gemini model, such as gemini-2.5-flash or google/gemini-2.5-flash."
        )
    return jai_req
