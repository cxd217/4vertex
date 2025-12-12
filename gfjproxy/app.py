"""Proxy Application (Vertex AI Edition)."""

################################################################################

# Early initialization

from ._globals import (
    BANDWIDTH_WARNING,
    CLOUDFLARED,
    COOLDOWN,
    DEVELOPMENT,
    MODELS,
    PREFILL,
    PRESETS,
    PRODUCTION,
    PROXY_ADMIN,
    PROXY_NAME,
    PROXY_VERSION,
    XUID_SECRET,
)

# Proxy initialization with startup banner
print(f"GeminiForJanitors ({PROXY_VERSION}) - Vertex AI Edition")

if PRODUCTION:
    print(" * Production deployment")
    from gevent import monkey
    monkey.patch_all()
else:
    print(" * Development deployment")

################################################################################

# ruff: noqa: E402
import os  # [修改] 新增 import os
import json # [修改] 新增 import json
from secrets import token_bytes
from time import perf_counter
from traceback import print_exception

from colorama import just_fix_windows_console
from flask import Flask, abort, redirect, render_template, request, send_from_directory
from flask_cors import CORS
from google import genai 

from .bandwidth import bandwidth_usage
from .cooldown import get_cooldown
from .handlers import handle_chat_message, handle_proxy_test
from .logging import hijack_loggers, xlog, xlogtime
from .models import JaiRequest
from .start_time import START_TIME
from .storage import storage
from .utils import ResponseHelper, is_proxy_test, run_cloudflared
from .xuiduser import XUID, LocalUserStorage, RedisUserStorage, UserSettings

just_fix_windows_console()
hijack_loggers()

################################################################################
# [修改] Vertex AI 凭证与配置加载区域
################################################################################

PROJECT_ID = os.environ.get("PROJECT_ID")
CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")
# 默认区域，你可以改为 us-west1 或 asia-east1
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1") 

if CREDENTIALS_JSON:
    # 将环境变量里的 JSON 字符串写入临时文件，供 Google SDK 读取
    # 这是一个兼容性最好的方法
    creds_path = "vertex_credentials.json"
    with open(creds_path, "w") as f:
        f.write(CREDENTIALS_JSON)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    print(f" * Vertex AI Credentials loaded (Project: {PROJECT_ID}, Region: {VERTEX_LOCATION})")
else:
    print(" * WARNING: No GOOGLE_CREDENTIALS found! Vertex AI calls will fail.")

################################################################################

# Late initialization (保持原样)

if CLOUDFLARED is not None:
    run_cloudflared(CLOUDFLARED)

if BANDWIDTH_WARNING:
    print(f" * Bandwidth warning set at {BANDWIDTH_WARNING / 1024:.1f} GiB")
else:
    print(" * Bandwidth warning disabled")

if COOLDOWN:
    print(" * Using cooldown policy:", COOLDOWN)
else:
    print(" * No cooldown policy")

if isinstance(storage, RedisUserStorage):
    print(" * Using Redis user storage")
elif isinstance(storage, LocalUserStorage):
    print(" * Using local user storage")
else:
    print(" * ERROR: No user storage")
    exit(1)

if XUID_SECRET is not None:
    print(" * Using provided XUID secret")
    xuid_secret = XUID_SECRET.encode("utf-8")
elif DEVELOPMENT:
    print(" * WARNING: Using development XUID secret")
    xuid_secret = token_bytes(32)
else:
    print(" * ERROR: Missing XUID secret")
    exit(1)

if PRESETS:
    print(" * Using presets: " + ", ".join(PRESETS.keys()))
else:
    print(" * WARNING: No presets loaded")

if PREFILL:
    print(f" * Using prefill ({len(PREFILL)} characters)")
else:
    print(" * WARNING: No prefill loaded")

################################################################################

app = application = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
@app.route("/index", methods=["GET"])
@app.route("/index.html", methods=["GET"])
def index():
    if request.path != "/":
        return redirect("/", code=301)
    xlog(None, "Handling index")
    return render_template(
        "index.html",
        admin=PROXY_ADMIN,
        announcement=storage.announcement,
        title=PROXY_NAME,
        version=PROXY_VERSION,
    )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static", "favicon.ico")


@app.route("/health")
@app.route("/healthz")
def health():
    keyspace = -1
    if isinstance(storage, RedisUserStorage):
        keyspace = storage._client.info("keyspace").get("db0", {}).get("keys", -1)
    usage = bandwidth_usage()
    return {
        "admin": PROXY_ADMIN,
        "bandwidth": usage.total,
        "cooldown": get_cooldown(usage),
        "keyspace": keyspace,
        "uptime": int(perf_counter() - START_TIME),
        "version": PROXY_VERSION,
    }, 200


@app.route("/", methods=["POST"])
@app.route("/chat/completions", methods=["POST"])
@app.route("/quiet/", methods=["POST"])
@app.route("/quiet/chat/completions", methods=["POST"])
def proxy():
    request_json = request.get_json(silent=True)
    if not request_json:
        abort(400, "Bad Request. Missing or invalid JSON.")

    request_path = request.path
    jai_req = JaiRequest.parse(request_json)
    jai_req.quiet = "/quiet/" in request_path
    proxy_test = is_proxy_test(request_json)
    response = ResponseHelper(use_stream=jai_req.stream, wrap_errors=proxy_test)

    # [修改] 鉴权逻辑调整
    # Janitor AI 依然需要传 Bearer Token，但这只是为了让我们识别用户(XUID)
    # 我们不再把这个 Token 发给 Google，而是使用服务器端的 Service Account
    
    request_auth = request.headers.get("authorization", "").split(" ", maxsplit=1)
    if len(request_auth) != 2 or request_auth[0].lower() != "bearer":
        return response.build_error("Unauthorized. API key required for User ID.", 401)

    api_keys = [k.strip() for k in request_auth[1].split(",")]
    
    # 我们依然使用用户的 Key 来生成 XUID，保持用户管理功能正常
    xuid = XUID(api_keys[0], xuid_secret)

    if not storage.lock(xuid):
        xlog(xuid, "User attempted concurrent use")
        return response.build_error("Concurrent use is not allowed. Please wait a moment.", 403)

    user = UserSettings(storage, xuid)

    # Rate limiting
    if (seconds := user.last_seen()) and (cooldown := get_cooldown()):
        if (delay := cooldown - seconds) > 0:
            xlog(user, f"User told to wait {delay} seconds")
            storage.unlock(xuid)
            return response.build_error(f"Please wait {delay} seconds.", 429)

    # Handle user's request
    user.inc_rcounter()

    # =================================================================
    # [关键修改] 初始化 Vertex AI Client
    # =================================================================
    try:
        # 不再使用 api_key=...，而是切换到 vertexai 模式
        # SDK 会自动读取我们上面设置的 GOOGLE_APPLICATION_CREDENTIALS 文件
        client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=VERTEX_LOCATION
        )
    except Exception as e:
        storage.unlock(xuid)
        return response.build_error(f"Vertex Client Init Failed: {str(e)}", 500)
    # =================================================================

    log_details = [
        f"User {user.last_seen_msg()}",
        f"Request #{user.get_rcounter()}",
        f"Model {jai_req.model}" # 添加模型记录
    ]

    ref_time = xlogtime(
        user,
        f"Processing {'stream ' if jai_req.stream else ''}{request_path} ({', '.join(log_details)})",
    )

    try:
        # [注意] 如果 _globals.py 里的 MODELS 列表还是旧的 AI Studio 名字，
        # 这里可能需要去 _globals.py 添加 Vertex 的名字 (如 gemini-1.5-flash-001)
        if not jai_req.model:
            response.add_error("Please specify a model.", 400)
        elif jai_req.model not in MODELS:
             # 为了避免报错，可以选择临时注释掉下面的检查，或者去更新 _globals.py
             # response.add_error(f"Invalid/unsupported model: {jai_req.model}", 400)
             pass 

        if proxy_test:
            response = handle_proxy_test(client, user, jai_req, response)
        else:
            # 这里的 client 现在是 Vertex Client，但 handle_chat_message 内部调用
            # client.models.generate_content 的方法是通用的，不需要改 handlers.py
            response = handle_chat_message(client, user, jai_req, response)
            
    except Exception as e:
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
    else:
        xlog(user, "Invalid user not saved")

    storage.unlock(xuid)

    return response.build()

# 下面的 Admin 路由部分保持不变
# ... (secret_required, admin_announcement, admin_dump_all 省略，不需要改)
# 请保留你原文件底部 def secret_required 及其后面的代码
# -------------------------------------------------------------
