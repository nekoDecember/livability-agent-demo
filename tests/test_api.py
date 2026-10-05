import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from livability_demo.api import create_app
from livability_demo.config import Settings
from livability_demo.models import AssessmentReport, CommanderNarrative
from livability_demo.offline_client import PAYLOAD_MARKER


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
            regions = await client.get(
                "/v1/agent/regions",
                headers={"Authorization": "Bearer test-secret"},
            )
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
    assert regions.status_code == 200
    assert {region["name"] for region in regions.json()["regions"]} >= {"流山市", "柏市"}
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
    assert "柏市 候補別の調査記録" in payload["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_assessment_stream_emits_progress_and_result(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            async with client.stream(
                "POST",
                "/v1/agent/assessments/stream",
                json={"request": "流山市を評価して"},
            ) as response:
                body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.status_code == 200
    assert "event: progress" in body
    assert "受付・計画" in body
    assert "event: result" in body
    assert '"report_id"' in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_assessment_api_supports_knowledge_only_mode(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/agent/assessments",
                json={
                    "request": "柏市を、車なし・子育てで評価して",
                    "mode": "knowledge_only",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report"]["plan"]["data_mode"] == "knowledge_only"
    assert "not-called" in payload["report"]["axis_results"][0]["api_calls"][0]["endpoint"]
    assert "LLM知識のみ" in payload["markdown"]


@pytest.mark.asyncio
async def test_assessment_api_applies_reviewed_weights(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/agent/assessments",
                json={
                    "request": "柏市を車なし・子育てで評価して",
                    "enabled_axes": ["convenience", "housing", "family"],
                    "weights": {"convenience": 10, "housing": 60, "family": 30},
                },
            )
    assert response.status_code == 200
    assert response.json()["report"]["plan"]["weights"] == {
        "convenience": 10.0,
        "housing": 60.0,
        "family": 30.0,
    }


@pytest.mark.asyncio
async def test_comparison_api_recommends_when_commander_model_fails(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, api_host="127.0.0.1"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            reports = []
            for name in ("高崎市", "前橋市"):
                assessed = await client.post(
                    "/v1/agent/assessments",
                    json={
                        "request": (
                            f"対象自治体コード: {'10202' if name == '高崎市' else '10201'}。"
                            f"{name}を評価。条件: 25歳・子育て世帯"
                        )
                    },
                )
                assert assessed.status_code == 200
                reports.append(assessed.json()["report"])

            class FailingCommander:
                async def run(self, prompt: str, *, session, options):
                    raise RuntimeError("provider details must not appear in user output")

                def create_session(self):
                    return None

            app.state.orchestrator.team.commander = FailingCommander()
            response = await client.post(
                "/v1/agent/comparisons",
                json={
                    "reports": reports,
                    "request": "25歳・子育て世帯",
                    "weights": {
                        "convenience": 10,
                        "housing": 15,
                        "family": 55,
                        "safety": 10,
                        "future": 10,
                    },
                },
            )

    assert response.status_code == 200
    comparison = response.json()["comparison"]
    assert comparison["recommended_region_code"] in {"10202", "10201"}, comparison
    assert (
        comparison["recommended_region_code"] == comparison["narrative"]["recommended_region_code"]
    )
    assert comparison["used_fallback"] is True
    assert set(comparison["shared_axes"]) == {
        "convenience",
        "housing",
        "family",
        "safety",
        "future",
    }
    assert "暫定提案" in comparison["narrative"]["summary"]
    assert "provider details" not in comparison["narrative"]["summary"]
    assert "provider details" not in comparison["narrative"]["summary"]
    assert comparison["narrative"]["reasons"]


@pytest.mark.asyncio
async def test_commander_can_select_against_weighted_axis_order_and_receives_all_findings(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            reports = []
            for name, code in (("高崎市", "10202"), ("前橋市", "10201")):
                assessed = await client.post(
                    "/v1/agent/assessments",
                    json={
                        "request": f"対象自治体コード: {code}。{name}を評価。条件: 25歳・子育て世帯"
                    },
                )
                assert assessed.status_code == 200
                reports.append(assessed.json()["report"])

            weights = {
                "convenience": 10,
                "housing": 15,
                "family": 55,
                "safety": 10,
                "future": 10,
            }
            validated_reports = [AssessmentReport.model_validate(item) for item in reports]
            baseline = await app.state.orchestrator.compare_candidates(
                validated_reports,
                user_request="25歳・子育て世帯",
                weights=weights,
            )
            comparable_weight = sum(weights[axis.value] for axis in baseline.shared_axes)
            arithmetic_scores = {
                report.plan.region.municipality_code: sum(
                    next(result for result in report.axis_results if result.axis == axis).score
                    * weights[axis.value]
                    / comparable_weight
                    for axis in baseline.shared_axes
                )
                for report in validated_reports
            }
            arithmetic_leader = max(arithmetic_scores, key=arithmetic_scores.get)
            commander_choice = next(code for code in arithmetic_scores if code != arithmetic_leader)

            class CommanderStub:
                def __init__(self):
                    self.calls = 0

                async def run(self, prompt: str, *, session, options):
                    self.calls += 1
                    payload = json.loads(prompt.split(PAYLOAD_MARKER, 1)[1])
                    assert "scores" not in payload
                    assert "selected_region_code" not in payload
                    assert payload["user_request"] == "25歳・子育て世帯"
                    assert payload["priority_weights"]["family"] == 55.0
                    assert all(
                        len(candidate["specialist_findings"]) == 5
                        for candidate in payload["candidates"]
                    )
                    if self.calls == 1:
                        return SimpleNamespace(
                            value=None,
                            text=json.dumps(
                                {
                                    "recommended_region_code": commander_choice,
                                    "summary": "この世帯条件では前橋市を提案します。",
                                    "reasons": ["子育て関連の根拠を確認しました。"],
                                    "tradeoffs": ["通勤時間は別途確認が必要です。"],
                                    "next_checks": ["通園距離を確認してください。แ้าว?"],
                                    "confidence": 0.62,
                                },
                                ensure_ascii=False,
                            ),
                        )
                    return SimpleNamespace(
                        value=CommanderNarrative(
                            recommended_region_code=commander_choice,
                            summary="この世帯条件では、候補地の子育て関連情報を踏まえて提案します。",
                            reasons=["子育ての条件に関係する専門Agentの根拠を確認しました。"],
                            tradeoffs=["未測定の保育空き状況は別途確認が必要です。"],
                            next_checks=["候補住居から保育施設までの距離を確認する"],
                            confidence=0.62,
                        ),
                        text="",
                    )

                def create_session(self):
                    return None

            commander_stub = CommanderStub()
            app.state.orchestrator.team.commander = commander_stub
            response = await client.post(
                "/v1/agent/comparisons",
                json={"reports": reports, "request": "25歳・子育て世帯", "weights": weights},
            )

    assert response.status_code == 200
    comparison = response.json()["comparison"]
    assert comparison["recommended_region_code"] == commander_choice
    assert comparison["recommended_region_code"] != arithmetic_leader
    assert comparison["narrative"]["summary"].startswith("この世帯条件では")
    assert comparison["used_fallback"] is True
    assert commander_stub.calls == 2
