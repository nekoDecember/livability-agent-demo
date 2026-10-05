from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from livability_demo import network


def test_resolve_proxy_environment_reads_both_casings_and_merges_no_proxy(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.backend"
    env_file.write_text(
        "http_proxy=http://dotenv-proxy:8080\n"
        "HTTPS_PROXY=http://dotenv-proxy:8443\n"
        "all_proxy=socks5://dotenv-proxy:1080\n"
        "no_proxy=dotenv.internal\n"
        "OPENAI_API_KEY=dotenv-only-secret\n",
        encoding="utf-8",
    )

    resolved = network.resolve_proxy_environment(
        env_file=env_file,
        environ={
            "HTTP_PROXY": "http://process-proxy:8080",
            "https_proxy": "http://process-proxy:8443",
            "NO_PROXY": "process.internal",
            "OPENAI_API_KEY": "process-only-secret",
        },
    )

    assert resolved["http_proxy"] == resolved["HTTP_PROXY"] == "http://process-proxy:8080"
    assert resolved["https_proxy"] == resolved["HTTPS_PROXY"] == "http://process-proxy:8443"
    assert resolved["all_proxy"] == resolved["ALL_PROXY"] == "socks5://dotenv-proxy:1080"
    assert set(resolved["NO_PROXY"].split(",")) == {
        "localhost",
        "127.0.0.1",
        "::1",
        "livability-agent-api",
        "openwebui",
        "dotenv.internal",
        "process.internal",
    }
    assert resolved["NO_PROXY"] == resolved["no_proxy"]
    assert "OPENAI_API_KEY" not in resolved


def test_http_proxy_is_used_for_https_when_https_proxy_is_unset(tmp_path: Path) -> None:
    resolved = network.resolve_proxy_environment(
        env_file=tmp_path / "missing.env",
        environ={"HTTP_PROXY": "http://proxy.internal:8080"},
    )

    assert resolved["http_proxy"] == resolved["HTTP_PROXY"]
    assert resolved["https_proxy"] == resolved["HTTPS_PROXY"]
    assert resolved["https_proxy"] == "http://proxy.internal:8080"


def test_explicit_https_proxy_takes_precedence_over_http_proxy(tmp_path: Path) -> None:
    resolved = network.resolve_proxy_environment(
        env_file=tmp_path / "missing.env",
        environ={
            "http_proxy": "http://proxy.internal:8080",
            "HTTPS_PROXY": "http://secure-proxy.internal:8443",
        },
    )

    assert resolved["https_proxy"] == resolved["HTTPS_PROXY"]
    assert resolved["https_proxy"] == "http://secure-proxy.internal:8443"


def test_configure_proxy_environment_exports_only_proxy_variables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_file = tmp_path / ".env.backend"
    env_file.write_text(
        "https_proxy=http://dotenv-proxy:8443\n"
        "NO_PROXY=custom.internal\n"
        "OPENAI_API_KEY=do-not-export\n",
        encoding="utf-8",
    )
    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    existing_key = os.environ.get("OPENAI_API_KEY")

    network.configure_proxy_environment(env_file=env_file)

    assert os.environ["https_proxy"] == os.environ["HTTPS_PROXY"] == "http://dotenv-proxy:8443"
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]
    assert "custom.internal" in os.environ["NO_PROXY"].split(",")
    assert "livability-agent-api" in os.environ["NO_PROXY"].split(",")
    assert os.environ.get("OPENAI_API_KEY") == existing_key


def test_async_client_factory_enables_environment_proxy_support(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(network, "configure_proxy_environment", lambda: {})
    monkeypatch.setattr(network.httpx, "AsyncClient", FakeAsyncClient)

    client = network.create_async_http_client(timeout=12.0)

    assert isinstance(client, FakeAsyncClient)
    assert captured == {"timeout": 12.0, "trust_env": True}


@pytest.mark.asyncio
async def test_async_client_sends_http_and_https_through_configured_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(network, "DEFAULT_ENV_FILE", tmp_path / "missing.env")
    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    request_lines: list[str] = []

    async def proxy_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            request_lines.append(request_line.decode("ascii").strip())
            while await reader.readline() not in (b"\r\n", b""):
                pass
            if request_line.startswith(b"CONNECT "):
                response = b"HTTP/1.1 200 Connection Established\r\n\r\n"
            else:
                response = (
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: close\r\n\r\nok"
                )
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(proxy_handler, "127.0.0.1", 0)
    proxy_port = server.sockets[0].getsockname()[1]
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    monkeypatch.setenv("http_proxy", proxy_url)
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")

    try:
        async with network.create_async_http_client(timeout=1.0) as client:
            response = await client.get("http://proxy-check.invalid/probe")
            assert response.status_code == 200
            assert response.text == "ok"

            with pytest.raises(httpx.HTTPError):
                await client.get("https://proxy-check.invalid/probe")
    finally:
        server.close()
        await server.wait_closed()

    assert request_lines[0] == "GET http://proxy-check.invalid/probe HTTP/1.1"
    assert request_lines[1] == "CONNECT proxy-check.invalid:443 HTTP/1.1"


def test_agent_team_closes_owned_openai_transport() -> None:
    from livability_demo.agent_team import AgentTeam
    from livability_demo.config import Settings

    async def run() -> None:
        team = AgentTeam(Settings(_env_file=None, llm_mode="openai", openai_api_key="test-key"))
        client = team._openai_client
        assert client is not None
        assert not client.is_closed()
        await team.close()
        assert client.is_closed()

    asyncio.run(run())
