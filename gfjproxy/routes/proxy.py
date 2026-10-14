from traceback import print_exception

from flask import Blueprint, request

from ..cooldown import get_cooldown
from ..handlers import handle_chat_message, handle_proxy_test
from ..logging import xlog, xlogtime
from ..request_validation import (
    ProxyAccessError,
    RequestValidationError,
    authorize_proxy_request,
    parse_valid_request,
)
from ..storage import storage
from ..utils import ResponseHelper, is_proxy_test
from ..xuid_secret import xuid_secret
from ..xuiduser import XUID, UserSettings

proxy = Blueprint("proxy", __name__)


@proxy.route("/", methods=["POST"])
@proxy.route("/chat/completions", methods=["POST"])
@proxy.route("/quiet/", methods=["POST"])
@proxy.route("/quiet/chat/completions", methods=["POST"])
def handle():
    # Authenticate before parsing messages, creating a user or calling Vertex.
    try:
        token = authorize_proxy_request(request.headers.get("authorization", ""))
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
    try:
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
            f"Model {jai_req.models['google']})",
        )

        try:
            if proxy_test:
                response = handle_proxy_test(user, jai_req, response)
            else:
                response = handle_chat_message(user, jai_req, response)
        except Exception as e:  # noqa: BLE001 -- report failures and release the user lock
            response.add_error("Internal Proxy Error", 500)
            print_exception(e)

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
        storage.unlock(xuid)
