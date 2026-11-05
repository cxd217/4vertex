from contextlib import ExitStack
from traceback import print_exception

import httpx2
from flask import Blueprint, Response, jsonify, request

from ..cooldown import get_cooldown
from ..handlers import LiveChatReply, handle_chat_message, handle_proxy_test
from ..logging import xlog, xlogtime
from ..models import JaiRequest
from ..providers.runapi import (
    RunAPIError,
    get_runapi_models,
    open_runapi_chat,
    runapi_error_message,
)
from ..providers.runapi_chat import RunAPIChat
from ..request_validation import (
    ProxyAccessError,
    RequestValidationError,
    authorize_proxy_request,
    configured_vertex_models,
    parse_runapi_request,
    parse_valid_request,
    select_runapi_key,
)
from ..statistics import track_stats
from ..storage import storage
from ..utils import ResponseHelper, is_proxy_test
from ..xuid_secret import xuid_secret
from ..xuiduser import XUID, UserSettings

proxy = Blueprint("proxy", __name__)


@proxy.route("/", methods=["POST"])
@proxy.route("/chat/completions", methods=["POST"])
@proxy.route("/v1/chat/completions", methods=["POST"])
@proxy.route("/quiet/", methods=["POST"])
@proxy.route("/quiet/chat/completions", methods=["POST"])
def handle(*, runapi_only: bool = False):
    # Choose the provider before parsing messages or using any credentials.
    authorization = request.headers.get("authorization", "")
    try:
        runapi_key = select_runapi_key(authorization, explicit=runapi_only)
        token = runapi_key or authorize_proxy_request(authorization)
    except ProxyAccessError as e:
        return ResponseHelper(wrap_errors=True).build_error(str(e), e.status)

    request_json = request.get_json(silent=True)
    if not isinstance(request_json, dict) or not request_json:
        return ResponseHelper(wrap_errors=True).build_error(
            "Bad Request. Missing or invalid JSON object.", 400
        )
    proxy_test = is_proxy_test(request_json)
    response = ResponseHelper(
        use_stream=request_json.get("stream") is True, wrap_errors=proxy_test
    )
    runapi = None
    try:
        if runapi_key:
            payload = parse_runapi_request(request_json)
            if runapi_key in payload["model"]:
                raise RequestValidationError(
                    "Enter a model ID in Model Name, not the API key."
                )
            if "/v1/" in request.path:
                return _handle_runapi_chat(runapi_key, payload, proxy_test, response)
            runapi = RunAPIChat(runapi_key, payload)
            jai_req = JaiRequest.parse(request_json)
            jai_req.models = {"runapi": payload["model"]}
        else:
            jai_req = parse_valid_request(request_json)
    except RequestValidationError as e:
        return response.build_error(str(e), 400)
    jai_req.quiet = "/quiet/" in request.path

    xuid = XUID(token, xuid_secret)
    if not storage.lock(xuid):
        xlog(xuid, "User attempted concurrent use")
        return response.build_error(
            "Concurrent use is not allowed. Please wait a moment.", 403
        )

    release_here = True
    try:
        user = UserSettings(storage, xuid)
        if (
            (seconds := user.last_seen())
            and (cooldown := get_cooldown())
            and (delay := cooldown - seconds) > 0
        ):
            xlog(user, f"User told to wait {delay} seconds")
            return response.build_error(f"Please wait {delay} seconds.", 429)

        user.inc_rcounter()
        ref_time = xlogtime(
            user,
            f"Processing {'stream ' if jai_req.stream else ''}{request.path} "
            f"(User {user.last_seen_msg()}, Request #{user.get_rcounter()}, "
            f"Model {next(iter(jai_req.models.values()))})",
        )

        try:
            provider_options = {"runapi": runapi} if runapi else {}
            if proxy_test:
                response = handle_proxy_test(
                    user, jai_req, response, **provider_options
                )
            else:
                response = handle_chat_message(
                    user, jai_req, response, **provider_options
                )
        except Exception as e:  # noqa: BLE001 -- report failures and release the user lock
            response.add_error("Internal Proxy Error", 500)
            print_exception(e)

        if isinstance(response, LiveChatReply):
            live_reply = response
            reply = Response(
                live_reply.body(lambda: storage.announcement),
                content_type="text/event-stream; charset=utf-8",
            )
            reply.headers["X-Accel-Buffering"] = "no"
            reply.headers["Cache-Control"] = "no-cache"

            def finalize():
                try:
                    live_reply.close()
                    if user.valid:
                        user.save()
                finally:
                    storage.unlock(xuid)

            reply.call_on_close(finalize)
            release_here = False
            return reply

        if 200 <= response.status <= 299:
            xlogtime(user, "Processing succeeded", ref_time)
            if not proxy_test and (announcement := storage.announcement):
                response.add_proxy_message(f"***\n{announcement}\n***")
        else:
            messages = response.message.split("\n")
            xlogtime(user, f"Processing failed: {messages[0]}", ref_time)
            for message in messages[1:]:
                xlog(user, f"> {message}")

        if user.valid:
            user.save()
        return response.build()
    finally:
        if release_here:
            storage.unlock(xuid)


@proxy.route("/runapi/", methods=["POST"])
@proxy.route("/runapi/chat/completions", methods=["POST"])
@proxy.route("/runapi/v1/chat/completions", methods=["POST"])
@proxy.route("/runapi/quiet/", methods=["POST"])
@proxy.route("/runapi/quiet/chat/completions", methods=["POST"])
def handle_runapi():
    return handle(runapi_only=True)


@proxy.get("/models")
@proxy.get("/v1/models")
@proxy.get("/runapi/models")
@proxy.get("/runapi/v1/models")
def models():
    authorization = request.headers.get("authorization", "")
    try:
        key = select_runapi_key(
            authorization, explicit=request.path.startswith("/runapi/")
        )
        if key:
            names, owner = get_runapi_models(key), "runapi"
        else:
            authorize_proxy_request(authorization)
            names, owner = configured_vertex_models(), "vertex"
        reply = jsonify(
            object="list",
            data=[
                {"id": model, "object": "model", "owned_by": owner} for model in names
            ],
        )
    except (ProxyAccessError, RunAPIError) as error:
        reply = jsonify(error={"message": str(error), "type": "proxy_error"})
        reply.status_code = error.status
    reply.headers["Cache-Control"] = "no-store"
    return reply


def _relay_runapi(upstream: httpx2.Response):
    try:
        yield from upstream.iter_bytes()
    except httpx2.HTTPError:
        # The 200 headers are already sent; emit a stream error rather than a new status.
        yield b'\n\ndata: {"error":{"message":"RunAPI stream interrupted."}}\n\n'


def _handle_runapi_chat(
    key: str, payload: dict, proxy_test: bool, response: ResponseHelper
):
    if proxy_test:
        payload["stream"] = False

    xuid = XUID(key, xuid_secret)
    if not storage.lock(xuid):
        return response.build_error(
            "Concurrent use is not allowed. Please wait a moment.", 403
        )

    with ExitStack() as cleanup:
        cleanup.callback(storage.unlock, xuid)
        user = UserSettings(storage, xuid)
        if (
            (seconds := user.last_seen()) is not None
            and (cooldown := get_cooldown())
            and (delay := cooldown - seconds) > 0
        ):
            return response.build_error(f"Please wait {delay} seconds.", 429)

        try:
            upstream = open_runapi_chat(key, payload)
            cleanup.callback(upstream.close)
            if payload["stream"]:
                if (
                    upstream.headers.get("content-type", "").split(";", 1)[0].strip()
                    != "text/event-stream"
                ):
                    raise RunAPIError("RunAPI did not return an event stream.", 502)
                reply = Response(
                    _relay_runapi(upstream), content_type="text/event-stream"
                )
                reply.headers["X-Accel-Buffering"] = "no"
            else:
                upstream.read()
                body = upstream.json()
                if (
                    not isinstance(body, dict)
                    or not isinstance(body.get("choices"), list)
                    or not body["choices"]
                ):
                    raise RunAPIError("RunAPI returned an invalid chat response.", 502)
                reply = response.build_message("TEST") if proxy_test else jsonify(body)
        except RunAPIError as error:
            track_stats("r.runapi.failed")
            return response.build_error(runapi_error_message(key, error), error.status)
        except httpx2.TimeoutException:
            track_stats("r.runapi.failed")
            return response.build_error("RunAPI timed out.", 504)
        except (httpx2.HTTPError, ValueError):
            track_stats("r.runapi.failed")
            return response.build_error(
                "RunAPI returned an unreadable chat response.", 502
            )

        track_stats("r.runapi.succeeded")
        user.inc_rcounter()
        user.save()
        if payload["stream"]:
            reply.call_on_close(cleanup.pop_all().close)
        return reply
