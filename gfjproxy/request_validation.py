"""Validate proxy access and text chat requests before calling Vertex AI."""

from hmac import compare_digest
from math import isfinite
from os import environ

from .models import JaiRequest
from .utils import comma_split


class ProxyAccessError(ValueError):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


class RequestValidationError(ValueError):
    """Invalid client input, with a message that never includes request content."""


def authorize_proxy_request(authorization: str) -> str:
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ProxyAccessError("Unauthorized. Proxy access token required.")
    tokens = comma_split(parts[1])
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
        if not any(
            compare_digest(token.encode("utf-8"), key.encode("utf-8"))
            for key in allowed_tokens
        ):
            raise ProxyAccessError("Unauthorized. Invalid proxy access token.")
    return token


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


def parse_valid_request(data: dict) -> JaiRequest:
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        raise RequestValidationError("model must be a non-empty Gemini model name.")
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
    jai_req = JaiRequest.parse(data)
    if jai_req.models.get("unknown") or not jai_req.models.get("google"):
        raise RequestValidationError(
            "Use one Vertex AI Gemini model, such as gemini-2.5-flash or google/gemini-2.5-flash."
        )
    return jai_req
