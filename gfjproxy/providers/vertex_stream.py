"""Consume Vertex AI events without exposing thought parts to the client."""

import json
from collections.abc import Generator
from typing import Any

import httpx2

from .._globals import PROCESS_TIMEOUT
from ..http_client import http_client
from ..models import JaiMessage, JaiResult
from ..statistics import track_stats
from ..vertex_auth import vertex_request_auth
from ..xuiduser import XUID
from .sse import sse_data
from .vertex import (
    build_vertex_payload,
    parse_vertex_response,
    vertex_request_failure,
    vertex_visible_text,
)


def vertex_stream_content(
    user: XUID,
    model: str,
    messages: list[JaiMessage],
    settings: dict[str, Any] | None = None,
) -> Generator[str | JaiResult]:
    """Yield text deltas, followed by one terminal result with usage and grounding."""
    aggregate: dict[str, Any] = {}
    candidate: dict[str, Any] = {}
    text_parts: list[str] = []
    queries: dict[str, None] = {}
    links: dict[str, None] = {}
    finish_reason = ""
    done = False

    try:
        url, headers = vertex_request_auth(model, stream=True)
        with http_client.stream(
            "POST",
            url,
            headers={**headers, "Accept": "text/event-stream"},
            json=build_vertex_payload(messages, settings),
            timeout=PROCESS_TIMEOUT,
        ) as response:
            if not response.is_success:
                response.read()
            response.raise_for_status()
            if (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "text/event-stream"
            ):
                raise ValueError("Expected a Vertex AI event stream")

            for data in sse_data(response):
                if data == "[DONE]":
                    done = True
                    break
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise TypeError("Invalid Vertex AI event")
                if "error" in event:
                    error = event["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    status = code if type(code) is int and 400 <= code < 600 else 502
                    raise httpx2.HTTPStatusError(
                        "Vertex AI sent a stream error",
                        request=response.request,
                        response=httpx2.Response(
                            status, json=event, request=response.request
                        ),
                    )
                for field in ("promptFeedback", "usageMetadata"):
                    if isinstance(event.get(field), dict):
                        aggregate.setdefault(field, {}).update(event[field])

                candidates = event.get("candidates")
                if (
                    not isinstance(candidates, list)
                    or not candidates
                    or not isinstance(candidates[0], dict)
                ):
                    continue
                current = candidates[0]
                if (
                    isinstance(reason := current.get("finishReason"), str)
                    and reason
                    and reason != "FINISH_REASON_UNSPECIFIED"
                ):
                    finish_reason = reason

                if isinstance(grounding := current.get("groundingMetadata"), dict):
                    if isinstance(items := grounding.get("webSearchQueries"), list):
                        queries.update(
                            dict.fromkeys(q for q in items if isinstance(q, str))
                        )
                    if isinstance(items := grounding.get("groundingChunks"), list):
                        for item in items:
                            web = item.get("web") if isinstance(item, dict) else None
                            if isinstance(web, dict) and isinstance(
                                uri := web.get("uri"), str
                            ):
                                links[uri] = None

                if text := vertex_visible_text(current):
                    text_parts.append(text)
                    yield text
    except Exception as error:  # noqa: BLE001 -- use the shared Vertex error mapping
        yield vertex_request_failure(user, model, error)
        return

    if text_parts and not (finish_reason or done):
        track_stats("g.failed.stream_incomplete")
        yield JaiResult(502, "Vertex AI ended the stream before completing the reply.")
        return

    candidate["content"] = {"parts": [{"text": "".join(text_parts).strip()}]}
    if finish_reason:
        candidate["finishReason"] = finish_reason
    if queries or links:
        candidate["groundingMetadata"] = {
            "webSearchQueries": list(queries),
            "groundingChunks": [{"web": {"uri": link}} for link in links],
        }
    aggregate["candidates"] = [candidate]
    result = parse_vertex_response(user, aggregate)
    result.metadata.finish_reason = finish_reason
    yield result
