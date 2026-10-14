from hmac import compare_digest
from os import environ
from traceback import print_exception

from flask import Blueprint, abort, request

from ..cooldown import get_cooldown
from ..handlers import handle_chat_message, handle_proxy_test
from ..logging import xlog, xlogtime
from ..models import JaiRequest
from ..storage import storage
from ..utils import ResponseHelper, comma_split, is_proxy_test
from ..xuid_secret import xuid_secret
from ..xuiduser import XUID, UserSettings

proxy = Blueprint("proxy", __name__)


@proxy.route("/", methods=["POST"])
@proxy.route("/chat/completions", methods=["POST"])
@proxy.route("/quiet/", methods=["POST"])
@proxy.route("/quiet/chat/completions", methods=["POST"])
def handle():
    request_json = request.get_json(silent=True)
    if not isinstance(request_json, dict) or not request_json:
        abort(400, "Bad Request. Missing or invalid JSON object.")

    try:
        jai_req = JaiRequest.parse(request_json)
    except (TypeError, ValueError):
        return ResponseHelper().build_error("Invalid model or messages.", 400)

    jai_req.quiet = "/quiet/" in request.path
    proxy_test = is_proxy_test(request_json)
    response = ResponseHelper(use_stream=jai_req.stream, wrap_errors=proxy_test)

    # Client tokens identify proxy settings; Google uses the server's credentials.
    request_auth = request.headers.get("authorization", "").split(maxsplit=1)
    if len(request_auth) != 2 or request_auth[0].lower() != "bearer":
        return response.build_error("Unauthorized. Proxy access token required.", 401)
    tokens = comma_split(request_auth[1])
    if not tokens:
        return response.build_error("Unauthorized. Proxy access token required.", 401)
    # Preserve the old XUID if the configuration still has comma-separated keys.
    token = tokens[0]
    allowed_tokens = comma_split(environ.get("GFJPROXY_ACCESS_KEYS", ""))
    if allowed_tokens and not any(
        compare_digest(token.encode("utf-8"), key.encode("utf-8"))
        for key in allowed_tokens
    ):
        return response.build_error("Unauthorized. Invalid proxy access token.", 401)

    if not jai_req.models:
        return response.build_error("Please specify a model.", 400)
    if jai_req.models.get("unknown"):
        return response.build_error(
            "Use one Vertex AI Gemini model, such as gemini-2.5-flash or google/gemini-2.5-flash.",
            400,
        )
    if not jai_req.messages:
        return response.build_error("Please include at least one message.", 400)

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
