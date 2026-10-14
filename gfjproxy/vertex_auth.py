"""Server-side Google credentials for Vertex AI, kept in memory."""

import json
import re
from functools import lru_cache
from os import environ
from threading import Lock

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_auth_lock = Lock()


class VertexConfigurationError(RuntimeError):
    """A configuration error whose message is safe to show to the client."""


@lru_cache(maxsize=1)
def _load_credentials() -> tuple[Credentials, str, str, Request]:
    credentials_json = environ.get("GOOGLE_CREDENTIALS")
    if credentials_json:
        try:
            info = json.loads(credentials_json)
            if not isinstance(info, dict) or info.get("type") != "service_account":
                raise ValueError("Expected a service account")
            if info.get("token_uri") != "https://oauth2.googleapis.com/token":
                raise ValueError("Expected Google's token endpoint")
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
            credential_project = info.get("project_id")
        except (ValueError, TypeError, KeyError) as e:
            raise VertexConfigurationError(
                "Invalid GOOGLE_CREDENTIALS. Supply the complete Google service-account JSON."
            ) from e
    else:
        try:
            credentials, credential_project = google.auth.default(scopes=SCOPES)
        except google.auth.exceptions.GoogleAuthError as e:
            raise VertexConfigurationError(
                "Vertex AI credentials missing. Set GOOGLE_CREDENTIALS or configure Application Default Credentials."
            ) from e

    project = (
        environ.get("PROJECT_ID")
        or environ.get("GOOGLE_CLOUD_PROJECT")
        or credential_project
    )
    if not project or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project):
        raise VertexConfigurationError(
            "Set PROJECT_ID to a valid Google Cloud project ID."
        )
    location = (
        environ.get("VERTEX_LOCATION")
        or environ.get("GOOGLE_CLOUD_LOCATION")
        or "global"
    ).lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", location):
        raise VertexConfigurationError("Invalid VERTEX_LOCATION.")
    return credentials, project, location, Request()


def vertex_request_auth(model: str) -> tuple[str, dict[str, str]]:
    """Refresh expiring credentials and return the Vertex URL and OAuth headers."""
    if not re.fullmatch(r"gemini-[a-z0-9][a-z0-9._-]*", model):
        raise VertexConfigurationError("Invalid Vertex AI Gemini model name.")
    with _auth_lock:
        credentials, project, location, auth_request = _load_credentials()
        if location == "global":
            host = "aiplatform.googleapis.com"
        elif location in ("us", "eu"):
            host = f"aiplatform.{location}.rep.googleapis.com"
        else:
            host = f"{location}-aiplatform.googleapis.com"
        url = (
            f"https://{host}/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent"
        )
        headers = {"Content-Type": "application/json"}
        credentials.before_request(auth_request, "POST", url, headers)
    return url, headers
