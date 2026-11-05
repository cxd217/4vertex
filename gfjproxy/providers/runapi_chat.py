"""Adapt RunAPI text replies to the shared JanitorAI command pipeline."""

import json
from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass, field

import httpx2

from ..models import JaiMessage, JaiResult, JaiResultMetadata, JaiResultTokenUsage
from .runapi import RunAPIError, open_runapi_chat, runapi_error_message
from .sse import sse_data


def _choice(body: dict) -> dict:
    choices = body.get("choices")
    return (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )


def _content(choice: dict, field: str) -> str:
    part = choice.get(field)
    text = part.get("content") if isinstance(part, dict) else None
    return text if isinstance(text, str) else ""


def _reply_result(text: str, usage: dict, reason: str) -> JaiResult:
    if not text.strip():
        return JaiResult(502, "RunAPI returned no reply text.")
    details = usage.get("completion_tokens_details")
    return JaiResult(
        200,
        text,
        metadata=JaiResultMetadata(
            finish_reason={"length": "MAX_TOKENS", "content_filter": "SAFETY"}.get(
                reason, "STOP"
            ),
            token_usage=JaiResultTokenUsage(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                reasoning_tokens=details.get("reasoning_tokens")
                if isinstance(details, dict)
                else None,
                total_tokens=usage.get("total_tokens"),
            )
            if usage
            else None,
        ),
    )


@dataclass(slots=True)
class RunAPIChat:
    key: str = field(repr=False)
    payload: dict = field(repr=False)

    def _request(
        self, messages: list[JaiMessage], settings: dict, *, stream: bool
    ) -> dict:
        data = {
            **self.payload,
            "messages": [
                {"role": msg.role, "content": msg.content} for msg in messages
            ],
            "stream": stream,
        }
        for name in ("temperature", "top_p", "frequency_penalty", "repetition_penalty"):
            if name in settings:
                data["presence_penalty" if name == "repetition_penalty" else name] = (
                    settings[name]
                )
        return data

    def _error(self, error: Exception) -> JaiResult:
        if isinstance(error, RunAPIError):
            return JaiResult(
                error.status,
                runapi_error_message(self.key, error),
                metadata=JaiResultMetadata(
                    api_key_valid=error.status not in (401, 403)
                ),
            )
        if isinstance(error, httpx2.TimeoutException):
            return JaiResult(504, "RunAPI timed out.")
        return JaiResult(502, "RunAPI returned an unreadable chat response.")

    def generate(self, messages: list[JaiMessage], settings: dict) -> JaiResult:
        try:
            with closing(
                open_runapi_chat(
                    self.key, self._request(messages, settings, stream=False)
                )
            ) as upstream:
                upstream.read()
                body = upstream.json()
                if not isinstance(body, dict):
                    raise TypeError("Invalid RunAPI response")
                choice = _choice(body)
                usage = body.get("usage")
                return _reply_result(
                    _content(choice, "message"),
                    usage if isinstance(usage, dict) else {},
                    choice.get("finish_reason") or "",
                )
        except (RunAPIError, httpx2.HTTPError, ValueError, TypeError) as error:
            return self._error(error)

    def stream(
        self, messages: list[JaiMessage], settings: dict
    ) -> Generator[str | JaiResult]:
        parts = []
        usage = {}
        reason = ""
        done = False
        try:
            with closing(
                open_runapi_chat(
                    self.key, self._request(messages, settings, stream=True)
                )
            ) as upstream:
                if (
                    upstream.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "text/event-stream"
                ):
                    raise RunAPIError("RunAPI did not return an event stream.", 502)
                for data in sse_data(upstream):
                    if data == "[DONE]":
                        done = True
                        break
                    event = json.loads(data)
                    if not isinstance(event, dict):
                        raise TypeError("Invalid RunAPI event")
                    if "error" in event:
                        error = event["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        status = (
                            code if type(code) is int and 400 <= code < 600 else 502
                        )
                        raise RunAPIError("RunAPI returned a stream error.", status)
                    if isinstance(event.get("usage"), dict):
                        usage.update(event["usage"])
                    choice = _choice(event)
                    if isinstance(value := choice.get("finish_reason"), str) and value:
                        reason = value
                    if text := _content(choice, "delta"):
                        parts.append(text)
                        yield text
        except (RunAPIError, httpx2.HTTPError, ValueError, TypeError) as error:
            yield self._error(error)
            return
        if parts and not (reason or done):
            yield JaiResult(502, "RunAPI ended the stream before completing the reply.")
        else:
            yield _reply_result("".join(parts), usage, reason)
