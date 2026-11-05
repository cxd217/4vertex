"""OpenAI-compatible requests to the explicitly configured RunAPI service."""

import httpx2

from .._globals import PROCESS_TIMEOUT
from ..request_validation import is_model_id

RUNAPI_BASE_URL = "https://api.runapi.sbs/v1/"
runapi_client = httpx2.Client(
    base_url=RUNAPI_BASE_URL,
    follow_redirects=False,
    trust_env=False,
)


class RunAPIError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _check_response(response: httpx2.Response):
    if response.is_success:
        return
    status = response.status_code
    message = {
        400: "RunAPI rejected the request. Check the model ID and generation settings.",
        401: "RunAPI rejected the API key.",
        403: "This RunAPI key does not have access to the requested resource.",
        404: "RunAPI could not find the requested model or endpoint.",
        429: "RunAPI rate limit or quota reached.",
        503: "RunAPI could not serve this request right now.",
    }.get(status, f"RunAPI returned HTTP {status}.")
    # Never follow redirects or return an upstream error body containing secrets.
    raise RunAPIError(message, status if status >= 400 else 502)


def _authorization(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def open_runapi_chat(key: str, payload: dict) -> httpx2.Response:
    try:
        request = runapi_client.build_request(
            "POST",
            "chat/completions",
            headers=_authorization(key),
            json=payload,
            timeout=httpx2.Timeout(PROCESS_TIMEOUT, connect=10),
        )
        request.headers.pop("cookie", None)
        response = runapi_client.send(request, stream=True)
    except httpx2.TimeoutException:
        raise RunAPIError("RunAPI timed out.", 504) from None
    except httpx2.HTTPError:
        raise RunAPIError("RunAPI is unreachable.", 503) from None
    try:
        _check_response(response)
    except RunAPIError:
        response.close()
        raise
    return response


def get_runapi_models(key: str) -> list[str]:
    try:
        request = runapi_client.build_request(
            "GET",
            "models",
            headers=_authorization(key) | {"Cache-Control": "no-cache"},
            timeout=5,
        )
        request.headers.pop("cookie", None)
        response = runapi_client.send(request)
        _check_response(response)
        body = response.json()
    except httpx2.TimeoutException:
        raise RunAPIError("RunAPI model list timed out.", 504) from None
    except httpx2.HTTPError:
        raise RunAPIError("RunAPI model list is unreachable.", 503) from None
    except ValueError:
        raise RunAPIError("RunAPI returned an invalid model list.", 502) from None

    entries = body.get("data") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise RunAPIError("RunAPI returned an invalid model list.", 502)
    return sorted(
        {
            entry["id"]
            for entry in entries
            if isinstance(entry, dict)
            and is_model_id(entry.get("id"))
            and key not in entry["id"]
        }
    )


def describe_runapi_error(key: str, error: RunAPIError) -> tuple[str, tuple[str, ...]]:
    message = str(error)
    names: tuple[str, ...] = ()
    if error.status in (404, 503):
        # Always fetch the catalog anew with this request's key. Never retry generation.
        try:
            names = tuple(get_runapi_models(key))
        except RunAPIError:
            message += "\n\nCould not retrieve the RunAPI model list for this key."
        else:
            if names:
                message += "\n\nRunAPI model IDs for this key:\n" + "\n".join(names)
            else:
                message += "\n\nRunAPI listed no models for this key."
    return message, names
