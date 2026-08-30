import asyncio
from pathlib import Path

import pytest

from livability_demo.config import Settings
from livability_demo.models import Axis
from livability_demo.orchestrator import LivabilityOrchestrator


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
        report, markdown = await orchestrator.assess(
            "流山市を子育て重視、車なしで評価して"
        )
    finally:
        await orchestrator.close()

    assert report.plan.region.name == "流山市"
    assert len(report.axis_results) == len(Axis)
    assert 0 <= report.overall_score <= 100
    assert "デモ用モックデータ" in markdown
    assert await asyncio.to_thread(Path(report.artifacts.markdown_path).exists)
    assert await asyncio.to_thread(Path(report.artifacts.json_path).exists)
    assert any(step.name == "5専門エージェント（並列）" for step in report.execution_steps)
    specialist_step = next(
        step for step in report.execution_steps if step.name == "5専門エージェント（並列）"
    )
    assert "WorkflowBuilder" in specialist_step.detail
    assert "fan-out/fan-in" in specialist_step.detail


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
        report, markdown = await orchestrator.assess(
            "流山市を評価して。安心・防災Agentは使わない"
        )
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
