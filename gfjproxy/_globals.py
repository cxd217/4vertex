"""Proxy global variables."""

import subprocess
from datetime import UTC, datetime, timedelta
from os import environ as _env
from os import scandir as _scandir
from os.path import dirname as _dirname

from .feature_guide import BANNER_FEATURE_GUIDE

################################################################################


CWD = _dirname(__file__)


def _fallback_env(*names) -> str | None:
    for name in names:
        # Check that the environment variable is not None and not the empty string
        if value := _env.get(name):
            return value
    return None


def _get_proxy_branch() -> str:
    branch = (
        _fallback_env(
            "GFJPROXY_BRANCH",
            "RAILWAY_GIT_BRANCH",
            "RENDER_GIT_BRANCH",
        )
        or "unknown"
    )

    try:
        res = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            cwd=CWD,
            text=True,
            check=True,
        )
        if resstr := res.stdout.strip():
            branch = resstr
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return branch


def _get_proxy_version() -> str:
    version = (
        _fallback_env(
            "GFJPROXY_VERSION",
            "RAILWAY_GIT_COMMIT_SHA",
            "RENDER_GIT_COMMIT",
        )
        or "unknown"
    )

    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%ct-%h"],
            capture_output=True,
            cwd=CWD,
            text=True,
            check=True,
        )
        if resstr := res.stdout.strip():
            timestamp, commit = resstr.split("-", maxsplit=1)

            # The previous code used "--date=format:%Y.%m.%d --format=%ad-%h" to format
            # the version string, leading to it being implicitly dependent on the timezone
            # of the committer. Since up to the time of writing, the only author has been
            # from the UTC-4 timezone, it is possible to maintain backwards compat with old
            # deployments by making the versioning scheme officially based on UTC-4 and
            # independent of the committer's timezone. This makes it possible to always
            # reliably derive a version from a commit hash and its UTC timestamp alone, such
            # as those provided from the GitHub APIs.

            dt = datetime.fromtimestamp(int(timestamp), tz=UTC) - timedelta(hours=4)

            version = f"{dt:%Y.%m.%d}-{commit}"
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return version


################################################################################

if _env.get("GFJPROXY_DEVELOPMENT"):
    DEVELOPMENT = True
    PRODUCTION = False
else:
    DEVELOPMENT = False
    PRODUCTION = True

################################################################################

CLOUDFLARED = _env.get("GFJPROXY_CLOUDFLARED")

PRESETS = {}
for entry in _scandir("presets"):
    if entry.is_file():
        with open(f"presets/{entry.name}", encoding="utf-8") as preset:
            PRESETS[entry.name.split(".")[0]] = preset.read()

PROXY_AUTHORS = [
    "@undefinedundefined (@undefined_anon on Discord, vu5eruz on GitHub)",
]

PROXY_ADMIN = _env.get("GFJPROXY_ADMIN", "Anonymous")

PROXY_NAME = "VertexaiForJanitors"

PROXY_BRANCH = _get_proxy_branch()

PROXY_VERSION = _get_proxy_version()

PROXY_URL = (
    _fallback_env(
        "GFJPROXY_EXTERNAL_URL",
        "RAILWAY_PUBLIC_DOMAIN",
        "RENDER_EXTERNAL_URL",
    )
    or "http://localhost:5000"
).rstrip("/")

COOLDOWN = _env.get("GFJPROXY_COOLDOWN", "0")

BANDWIDTH_WARNING = int(
    _env.get("GFJPROXY_BANDWIDTH_WARNING", "2560")
)  # 2.5 GiB in MiB

RENDER_API_KEY = _env.get("GFJPROXY_RENDER_API_KEY")

RENDER_SERVICE_ID = _env.get("RENDER_SERVICE_ID")

REDIS_URL = _env.get("GFJPROXY_REDIS_URL")

XUID_SECRET = _env.get("GFJPROXY_XUID_SECRET")

STATS_DURATION = int(_env.get("GFJPROXY_STATS_DURATION", "24"))

################################################################################

# Changing this has an impact on whether the runner (specifically gunicorn) will
# forcefully reset a worker after taking too long to answer a request. When
# deploying using gunicorn, make sure to provide a -t value larger than the one
# in here, to prevent issues from arising at run-time.
PROCESS_TIMEOUT: int = max(
    int(_env.get("GFJPROXY_PROCESS_TIMEOUT", "300")) - 10,
    60,
)

################################################################################

BANNER_VERSION = 39


def _make_banner(connection: str, quiet_path: str) -> str:
    return f"""***
# **{PROXY_NAME}** ({PROXY_VERSION} {PROXY_BRANCH})
*Admin: {PROXY_ADMIN}*

{connection}

{BANNER_FEATURE_GUIDE}

Service status: `{PROXY_URL}/health`
Request statistics: `{PROXY_URL}/stats`
Proxy URL without the automatic banner: `{PROXY_URL}{quiet_path}`
***
"""


BANNER = _make_banner(
    "This chat uses Google Cloud Vertex AI with server-side credentials.\n"
    "Your JanitorAI API key is used for proxy access and user identification. It is not sent to Google.\n"
    "Enter a Gemini model ID available to your project and region. The `google/`, `vertex/`, and `vertexai/` prefixes are optional.",
    "/quiet/",
)

RUNAPI_BANNER = _make_banner(
    "This chat uses RunAPI with the API key supplied in JanitorAI.\n"
    "Enter the complete model ID from your RunAPI catalog. If a model returns HTTP 503 or 404, the proxy fetches the current list for your key.",
    "/runapi/quiet/",
)
