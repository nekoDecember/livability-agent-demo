import asyncio
from pathlib import Path

import httpx
import pytest

from livability_demo.api import create_app
from livability_demo.config import Settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="mock",
        outputs_dir=tmp_path,
        mock_latency_ms=0,
        devui_auto_open=False,
        **overrides,
    )


def test_non_loopback_api_requires_a_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_host="0.0.0.0")
    with pytest.raises(ValueError, match="LIVABILITY_API_KEY"):
        settings.validate_api_runtime()


@pytest.mark.asyncio
async def test_assessment_api_auth_and_artifact_output(tmp_path: Path) -> None:
    app = create_app(
        _settings(
            tmp_path,
            api_host="0.0.0.0",
            livability_api_key="test-secret",
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            health = await client.get("/healthz")
            unauthorized = await client.get("/v1/models")
            response = await client.post(
                "/v1/agent/assessments",
                headers={"Authorization": "Bearer test-secret"},
                json={"request": "流山市を評価して。安心・防災Agentは使わない"},
            )

    assert health.json() == {
        "status": "ok",
        "llm_mode": "mock",
        "data_mode": "mock",
    }
    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["plan"]["excluded_axes"] == ["safety"]
    assert "使用（4）" in payload["markdown"]
    markdown_path = Path(payload["report"]["artifacts"]["markdown_path"])
    json_path = Path(payload["report"]["artifacts"]["json_path"])
    assert await asyncio.to_thread(markdown_path.is_file)
    assert await asyncio.to_thread(json_path.is_file)


@pytest.mark.asyncio
async def test_openai_compatible_chat_completion(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "livability-agent",
                    "messages": [{"role": "user", "content": "柏市を評価して"}],
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert "柏市 住みやすさ総合評価" in payload["choices"][0]["message"]["content"]
