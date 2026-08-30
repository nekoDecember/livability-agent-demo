from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "scripts/register_openwebui_pipe.py"


def load_registration_module():
    spec = importlib.util.spec_from_file_location("register_openwebui_pipe", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, payload))
        if path == "/api/v1/functions/list":
            return self.existing
        if path.endswith("/update") and "/valves/" not in path:
            return {"id": "livability_agent", "is_active": True}
        if path == "/api/v1/functions/create":
            return {"id": "livability_agent", "is_active": False}
        return {}


class FakeAuthClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any] | None, bool]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> Any:
        self.calls.append((method, path, payload, authenticated))
        return self.response


def test_client_rejects_non_http_openwebui_url() -> None:
    module = load_registration_module()
    with pytest.raises(module.RegistrationError, match="HTTP"):
        module.OpenWebUIClient("file:///tmp/openwebui", "secret")


def test_admin_credentials_are_exchanged_for_ephemeral_token(monkeypatch) -> None:
    module = load_registration_module()
    monkeypatch.delenv("OPENWEBUI_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("WEBUI_ADMIN_EMAIL", "admin@example.test")
    monkeypatch.setenv("WEBUI_ADMIN_PASSWORD", "not-logged-password")
    client = FakeAuthClient({"token": "ephemeral-jwt", "role": "admin"})

    token = module.resolve_admin_token(client)

    assert token == "ephemeral-jwt"
    assert client.calls == [
        (
            "POST",
            "/api/v1/auths/signin",
            {"email": "admin@example.test", "password": "not-logged-password"},
            False,
        )
    ]


def test_explicit_admin_token_remains_supported(monkeypatch) -> None:
    module = load_registration_module()
    monkeypatch.setenv("OPENWEBUI_ADMIN_TOKEN", "configured-token")
    client = FakeAuthClient(None)

    assert module.resolve_admin_token(client) == "configured-token"
    assert client.calls == []


def test_non_admin_signin_is_rejected_without_leaking_password() -> None:
    module = load_registration_module()
    password = "should-never-appear-in-errors"
    client = FakeAuthClient({"token": "user-jwt", "role": "user"})

    with pytest.raises(module.RegistrationError) as exc_info:
        module.sign_in_as_admin(client, "user@example.test", password)

    assert "administrator" in str(exc_info.value)
    assert password not in str(exc_info.value)


def test_openwebui_readiness_is_retried(monkeypatch) -> None:
    module = load_registration_module()
    calls = 0
    sleeps: list[float] = []

    class ReadinessClient:
        def request(self, method: str, path: str, *, authenticated: bool = True) -> Any:
            nonlocal calls
            calls += 1
            assert (method, path, authenticated) == ("GET", "/health", False)
            if calls < 3:
                raise module.RegistrationError("not ready")
            return {"status": True}

    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    module.wait_for_openwebui(ReadinessClient(), attempts=3, retry_seconds=0.25)

    assert calls == 3
    assert sleeps == [0.25, 0.25]


def test_registration_creates_configures_and_activates_pipe() -> None:
    module = load_registration_module()
    client = FakeClient([])
    action = module.register_pipe(
        client,
        content="class Pipe: pass",
        backend_url="http://livability-agent-api:8091/",
        backend_api_key="secret",
        timeout_seconds=300,
    )

    assert action == "created"
    paths = [path for _, path, _ in client.calls]
    assert "/api/v1/functions/create" in paths
    assert "/api/v1/functions/id/livability_agent/valves/update" in paths
    assert "/api/v1/functions/id/livability_agent/toggle" in paths
    valves = next(payload for _, path, payload in client.calls if path.endswith("/valves/update"))
    assert valves == {
        "backend_url": "http://livability-agent-api:8091",
        "backend_api_key": "secret",
        "timeout_seconds": 300,
    }


def test_registration_updates_active_pipe_without_toggling() -> None:
    module = load_registration_module()
    client = FakeClient([{"id": "livability_agent", "is_active": True}])
    action = module.register_pipe(
        client,
        content="class Pipe: pass",
        backend_url="http://another-agent:8091",
        backend_api_key="secret",
        timeout_seconds=120,
    )

    assert action == "updated"
    paths = [path for _, path, _ in client.calls]
    assert "/api/v1/functions/id/livability_agent/update" in paths
    assert "/api/v1/functions/id/livability_agent/toggle" not in paths
