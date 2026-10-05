import asyncio
import csv
import json
from pathlib import Path

import pytest

from livability_demo.config import Settings
from livability_demo.models import Axis
from livability_demo.open_data import build_open_data_snapshot
from livability_demo.orchestrator import LivabilityOrchestrator


def _build_takasaki_maebashi_snapshot(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    original = root / "synthetic-source.csv"
    original.write_text("synthetic fixture\n", encoding="utf-8")
    sources = root / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "SYNTHETIC-CITY-METRICS",
                        "source_name": "合成自治体指標",
                        "publisher": "テスト",
                        "download_page_url": "https://dashboard.e-stat.go.jp/static/api",
                        "license_id": "COMMERCIAL-USE-ALLOWED",
                        "license_url": "https://dashboard.e-stat.go.jp/static/terms",
                        "attribution": "テスト用の合成データ。実在の統計値ではありません。",
                        "retrieved_at": "2026-10-01",
                        "reference_date": "合成値",
                        "original_file": original.name,
                        "commercial_use_note": "合成データのみ",
                        "third_party_rights_note": "実データ・第三者データ不使用",
                        "transformation_note": "エンドツーエンドテスト用の合成データ",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metric_values = (
        ("convenience", "retail_density", 8.2, 9.1),
        ("convenience", "daytime_population_ratio", 97.0, 101.0),
        ("housing", "housing_stock", 1_050.0, 1_100.0),
        ("housing", "residential_land_price", 51_000.0, 50_000.0),
        ("family", "clinics_per_100k", 90.0, 95.0),
        ("family", "nursery_per_1000_children", 1.2, 1.1),
        ("family", "schools_per_1000_children", 0.6, 0.7),
        ("family", "tertiary_education_campuses", 3.0, 2.0),
        ("future", "population_retention_2040", 91.0, 88.0),
        ("future", "population_retention_2050", 84.0, 80.0),
    )
    metrics = root / "metrics.csv"
    with metrics.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "region_code",
                "region_name",
                "prefecture",
                "comparison_group",
                "axis",
                "metric_code",
                "value",
                "quality",
                "source_id",
                "sample_count",
                "note",
            ],
        )
        writer.writeheader()
        for axis, metric_code, takasaki_value, maebashi_value in metric_values:
            for code, name, value in (
                ("10202", "高崎市", takasaki_value),
                ("10201", "前橋市", maebashi_value),
            ):
                writer.writerow(
                    {
                        "region_code": code,
                        "region_name": name,
                        "prefecture": "群馬県",
                        "comparison_group": "テスト用の同一比較群",
                        "axis": axis,
                        "metric_code": metric_code,
                        "value": value,
                        "quality": 0.9,
                        "source_id": "SYNTHETIC-CITY-METRICS",
                        "sample_count": "",
                        "note": "合成値。実在の統計値ではありません。",
                    }
                )

    snapshot = root / "snapshot"
    build_open_data_snapshot(
        metrics_path=metrics,
        sources_path=sources,
        output_dir=snapshot,
    )
    return snapshot


@pytest.mark.asyncio
async def test_single_household_does_not_score_childcare_metrics(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="mock",
        outputs_dir=tmp_path,
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, _ = await orchestrator.assess(
            "流山市を1軸（医療・子育て）で評価して。条件: 25歳・独身・車通勤・勤務地:高崎駅周辺",
            enabled_axes=[Axis.FAMILY],
        )
    finally:
        await orchestrator.close()

    family = report.axis_results[0]
    childcare = {"nursery_per_1000_children", "schools_per_1000_children"}
    assert all(
        metric.direction == "context_only"
        for metric in family.metrics
        if metric.metric_code in childcare
    )
    assert not any(metric.metric_code == "tertiary_education_campuses" for metric in family.metrics)
    clinics = next(metric for metric in family.metrics if metric.metric_code == "clinics_per_100k")
    emergency = next(
        metric for metric in family.metrics if metric.metric_code == "emergency_hospitals"
    )
    expected = (
        clinics.normalized_score * clinics.weight + emergency.normalized_score * emergency.weight
    ) / (clinics.weight + emergency.weight)
    assert family.score == round(expected, 1)


@pytest.mark.asyncio
async def test_mock_assessment_writes_markdown_and_json(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="mock",
        outputs_dir=tmp_path,
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, markdown = await orchestrator.assess("流山市を子育て重視、車なしで評価して")
    finally:
        await orchestrator.close()

    assert report.plan.region.name == "流山市"
    assert len(report.axis_results) == len(Axis)
    assert 0 <= report.research_confidence <= 1
    assert "総合スコア" not in markdown
    assert "候補間の提案は、司令塔" in markdown
    assert "デモ用モックデータ" in markdown
    assert await asyncio.to_thread(Path(report.artifacts.markdown_path).exists)
    assert await asyncio.to_thread(Path(report.artifacts.json_path).exists)
    assert any(step.name == "5専門エージェント（並列）" for step in report.execution_steps)
    specialist_step = next(
        step for step in report.execution_steps if step.name == "5専門エージェント（並列）"
    )
    assert "WorkflowBuilder" in specialist_step.detail
    assert "fan-out/fan-in" in specialist_step.detail
    assert "担当データを個別分析（データ取得は軸ごとに並列）" in specialist_step.detail


@pytest.mark.asyncio
async def test_takasaki_maebashi_snapshot_produces_an_offline_commander_recommendation(
    tmp_path: Path,
) -> None:
    snapshot = _build_takasaki_maebashi_snapshot(tmp_path / "fixture")
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="open_data",
        open_data_dir=snapshot,
        outputs_dir=tmp_path / "outputs",
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        reports = []
        for name, code in (("高崎市", "10202"), ("前橋市", "10201")):
            report, markdown = await orchestrator.assess(
                f"対象自治体コード: {code}。{name}を評価。条件: 25歳・子育て世帯"
            )
            assert report.plan.region.name == name
            assert report.plan.data_mode == "open_data"
            assert "総合スコア" not in markdown
            assert "候補間の提案は、司令塔" in markdown
            usable = [
                metric
                for result in report.axis_results
                for metric in result.metrics
                if metric.quality > 0 and metric.direction != "context_only"
            ]
            assert len(usable) == 9
            reports.append(report)

        comparison = await orchestrator.compare_candidates(
            reports,
            user_request="25歳・子育て世帯",
            weights={axis: 20.0 for axis in Axis},
        )
    finally:
        await orchestrator.close()

    assert comparison.shared_axes == [
        Axis.CONVENIENCE,
        Axis.HOUSING,
        Axis.FAMILY,
        Axis.FUTURE,
    ]
    assert comparison.recommended_region_code in {"10202", "10201"}
    assert comparison.used_fallback is True
    assert "暫定提案" in comparison.narrative.summary


@pytest.mark.asyncio
async def test_knowledge_only_assessment_skips_regional_data_apis(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="government_api",
        # The knowledge-only request must not need live provider credentials.
        estat_app_id="unused-in-this-path",
        reinfolib_api_key="unused-in-this-path",
        outputs_dir=tmp_path,
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, markdown = await orchestrator.assess(
            "流山市を子育て重視、車なしで評価して",
            mode="knowledge_only",
        )
    finally:
        await orchestrator.close()

    assert report.plan.data_mode == "knowledge_only"
    assert len(report.axis_results) == len(Axis)
    assert all(
        call.status == "skipped" for result in report.axis_results for call in result.api_calls
    )
    assert any(
        step.status == "skipped" and step.name == "地域データAPI群"
        for step in report.execution_steps
    )
    assert "外部データAPIを使わず" in markdown
    assert "LLM知識のみ" in markdown


@pytest.mark.asyncio
async def test_excluded_agent_is_removed_from_workflow_data_and_report(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="mock",
        outputs_dir=tmp_path,
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        workflow = orchestrator.team.build_specialist_workflow(
            [Axis.CONVENIENCE, Axis.HOUSING, Axis.FAMILY, Axis.FUTURE]
        )
        report, markdown = await orchestrator.assess("流山市を評価して。安心・防災Agentは使わない")
    finally:
        await orchestrator.close()

    assert "SafetyAndDisasterAgent" not in workflow.executors
    assert report.plan.enabled_axes == [
        Axis.CONVENIENCE,
        Axis.HOUSING,
        Axis.FAMILY,
        Axis.FUTURE,
    ]
    assert report.plan.excluded_axes == [Axis.SAFETY]
    assert {result.axis for result in report.axis_results} == set(report.plan.enabled_axes)
    assert sum(report.plan.weights.values()) == 100
    assert not any(step.name == "安心・防災データAPI" for step in report.execution_steps)
    assert any(
        step.status == "skipped" and step.name.startswith("安心・防災")
        for step in report.execution_steps
    )
    assert any(step.name == "4専門エージェント（並列）" for step in report.execution_steps)
    assert "使用（4）" in markdown
    assert "除外（1）: 安心・防災" in markdown
    assert "### 安心・防災" not in markdown


@pytest.mark.asyncio
async def test_single_enabled_axis_runs_without_fan_out(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="mock",
        outputs_dir=tmp_path,
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, markdown = await orchestrator.assess(
            "流山市を移動・買い物だけで評価して",
            enabled_axes=[Axis.CONVENIENCE],
        )
    finally:
        await orchestrator.close()

    assert report.plan.enabled_axes == [Axis.CONVENIENCE]
    assert [result.axis for result in report.axis_results] == [Axis.CONVENIENCE]
    assert report.plan.weights == {Axis.CONVENIENCE: 100.0}
    assert any(step.name == "1専門エージェント（単独）" for step in report.execution_steps)
    assert "使用（1）: 移動・買い物" in markdown
