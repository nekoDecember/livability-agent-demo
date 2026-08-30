from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FUNCTION_ID = "livability_agent"
FUNCTION_NAME = "地域住みやすさ評価マルチエージェント"


class RegistrationError(RuntimeError):
    pass


class OpenWebUIClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        parsed_url = urlsplit(normalized_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise RegistrationError("OPENWEBUI_URL must be an absolute HTTP(S) URL.")
        self.base_url = normalized_url
        self.token = token
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self.token:
                raise RegistrationError("OpenWebUI admin authentication is required.")
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(body).get("detail")
            except (json.JSONDecodeError, AttributeError):
                detail = None
            raise RegistrationError(
                f"OpenWebUI API returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RegistrationError(f"Cannot reach OpenWebUI: {exc.reason}") from exc
        return json.loads(body) if body else None


def register_pipe(
    client: OpenWebUIClient,
    *,
    content: str,
    backend_url: str,
    backend_api_key: str,
    timeout_seconds: float,
) -> str:
    functions = client.request("GET", "/api/v1/functions/list")
    if not isinstance(functions, list):
        raise RegistrationError("OpenWebUI returned an invalid Function list.")
    existing = next(
        (item for item in functions if isinstance(item, dict) and item.get("id") == FUNCTION_ID),
        None,
    )
    form = {
        "id": FUNCTION_ID,
        "name": FUNCTION_NAME,
        "content": content,
        "meta": {
            "description": ("Microsoft Agent Frameworkの住みやすさ評価Workflowを独立API経由で実行")
        },
    }
    if existing:
        function = client.request("POST", f"/api/v1/functions/id/{FUNCTION_ID}/update", form)
        action = "updated"
    else:
        function = client.request("POST", "/api/v1/functions/create", form)
        action = "created"

    if not isinstance(function, dict):
        raise RegistrationError("OpenWebUI did not return the registered Function.")

    client.request(
        "POST",
        f"/api/v1/functions/id/{FUNCTION_ID}/valves/update",
        {
            "backend_url": backend_url.rstrip("/"),
            "backend_api_key": backend_api_key,
            "timeout_seconds": timeout_seconds,
        },
    )
    if not function.get("is_active", False):
        client.request("POST", f"/api/v1/functions/id/{FUNCTION_ID}/toggle")
    return action


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RegistrationError(f"{name} is required.")
    return value


def _env_or_default(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def wait_for_openwebui(
    client: OpenWebUIClient,
    *,
    attempts: int,
    retry_seconds: float,
) -> None:
    if attempts < 1:
        raise RegistrationError("OPENWEBUI_REGISTRATION_ATTEMPTS must be at least 1.")
    if retry_seconds < 0:
        raise RegistrationError("OPENWEBUI_REGISTRATION_RETRY_SECONDS cannot be negative.")

    last_error: RegistrationError | None = None
    for attempt in range(attempts):
        try:
            client.request("GET", "/health", authenticated=False)
            return
        except RegistrationError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
    raise RegistrationError(
        f"OpenWebUI did not become ready after {attempts} attempts."
    ) from last_error


def sign_in_as_admin(client: OpenWebUIClient, email: str, password: str) -> str:
    try:
        result = client.request(
            "POST",
            "/api/v1/auths/signin",
            {"email": email, "password": password},
            authenticated=False,
        )
    except RegistrationError as exc:
        raise RegistrationError("OpenWebUI administrator sign-in failed.") from exc
    token = result.get("token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise RegistrationError("OpenWebUI sign-in did not return an access token.")
    if result.get("role") != "admin":
        raise RegistrationError("OpenWebUI credentials do not belong to an administrator.")
    return token


def resolve_admin_token(client: OpenWebUIClient) -> str:
    configured_token = os.getenv("OPENWEBUI_ADMIN_TOKEN", "").strip()
    if configured_token:
        return configured_token
    return sign_in_as_admin(
        client,
        _required_env("WEBUI_ADMIN_EMAIL"),
        _required_env("WEBUI_ADMIN_PASSWORD"),
    )


def main() -> None:
    try:
        pipe_path = Path(os.getenv("PIPE_PATH", "/registration/livability_agent_pipe.py"))
        content = pipe_path.read_text(encoding="utf-8")
        base_url = _env_or_default("OPENWEBUI_URL", "http://openwebui:8080")
        request_timeout = float(os.getenv("OPENWEBUI_REQUEST_TIMEOUT_SECONDS", "30"))
        anonymous_client = OpenWebUIClient(base_url, timeout_seconds=request_timeout)
        wait_for_openwebui(
            anonymous_client,
            attempts=int(os.getenv("OPENWEBUI_REGISTRATION_ATTEMPTS", "30")),
            retry_seconds=float(os.getenv("OPENWEBUI_REGISTRATION_RETRY_SECONDS", "2")),
        )
        client = OpenWebUIClient(
            base_url,
            resolve_admin_token(anonymous_client),
            timeout_seconds=request_timeout,
        )
        action = register_pipe(
            client,
            content=content,
            backend_url=_env_or_default(
                "LIVABILITY_BACKEND_URL", "http://livability-agent-api:8091"
            ),
            backend_api_key=_required_env("LIVABILITY_API_KEY"),
            timeout_seconds=float(os.getenv("LIVABILITY_PIPE_TIMEOUT_SECONDS", "300")),
        )
    except (OSError, ValueError, RegistrationError) as exc:
        print(f"Pipe registration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"OpenWebUI Pipe {FUNCTION_ID} {action} and activated.")


if __name__ == "__main__":
    main()
