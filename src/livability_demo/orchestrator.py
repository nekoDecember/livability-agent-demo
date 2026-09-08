from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from time import perf_counter
from uuid import uuid4

from .agent_team import AgentTeam
from .config import Settings
from .data_providers import RegionalDataProvider, build_data_provider
from .models import (
    AXIS_LABELS,
    AssessmentReport,
    Axis,
    AxisEvidence,
    ExecutionStep,
    RegionInfo,
)
from .planning import build_plan
from .report_writer import ReportWriter
from .scoring import score_axis, score_overall

ProgressCallback = Callable[[str], Awaitable[None]]


async def _noop_progress(_: str) -> None:
    return None


class LivabilityOrchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        provider: RegionalDataProvider | None = None,
        team: AgentTeam | None = None,
    ) -> None:
        self.settings = settings
        self.settings.validate_runtime()
        self.provider = provider or build_data_provider(settings)
        self.team = team or AgentTeam(settings)
        self.writer = ReportWriter(settings.outputs_dir)

    async def assess(
        self,
        user_request: str,
        *,
        progress: ProgressCallback | None = None,
        enabled_axes: Iterable[Axis] | None = None,
    ) -> tuple[AssessmentReport, str]:
        notify = progress or _noop_progress
        started = perf_counter()
        steps: list[ExecutionStep] = []

        plan_started = perf_counter()
        region = await asyncio.wait_for(
            self.provider.resolve_region(user_request),
            timeout=self.settings.data_timeout_seconds,
        )
        plan = build_plan(
            user_request,
            region,
            data_mode=self.settings.data_mode,
            enabled_axes=enabled_axes,
        )
        plan_ms = int((perf_counter() - plan_started) * 1000)
        steps.append(
            ExecutionStep(
                name="受付・計画エージェント",
                status="completed",
                elapsed_ms=plan_ms,
                detail=(
                    f"{region.name} / {plan.agent_selection_reason} / "
                    f"{plan.weight_reason}"
                ),
            )
        )
        await notify(
            f"受付・計画: {region.name}を特定し、"
            f"{plan.agent_selection_reason}（{plan_ms} ms）"
        )

        await notify(
            f"{len(plan.enabled_axes)}領域のAPI/MCPデータ取得を並列開始しました"
        )
        evidence_by_axis = await self._fetch_all_axes(
            region,
            plan.enabled_axes,
            notify,
        )
        data_elapsed = max(evidence.elapsed_ms for evidence in evidence_by_axis.values())
        for axis in plan.enabled_axes:
            evidence = evidence_by_axis[axis]
            endpoints = ", ".join(call.endpoint for call in evidence.api_calls)
            steps.append(
                ExecutionStep(
                    name=f"{AXIS_LABELS[axis]}データAPI",
                    status="completed",
                    elapsed_ms=evidence.elapsed_ms,
                    detail=endpoints,
                )
            )
        api_count = sum(len(item.api_calls) for item in evidence_by_axis.values())
        steps.append(
            ExecutionStep(
                name="地域データAPI群",
                status="completed",
                elapsed_ms=data_elapsed,
                detail=(
                    f"{len(plan.enabled_axes)}領域・{api_count} APIツールを並列取得"
                ),
            )
        )
        for axis in plan.excluded_axes:
            steps.append(
                ExecutionStep(
                    name=f"{AXIS_LABELS[axis]}データAPI・専門エージェント",
                    status="skipped",
                    elapsed_ms=0,
                    detail="Agent構成指定によりAPI取得・分析・採点を未実行",
                )
            )

        agents_started = perf_counter()
        await notify(
            "WorkflowBuilderで"
            f"{len(plan.enabled_axes)}専門エージェントへfan-outし、並列実行しています"
        )
        narratives, used_fallback, detail = await self.team.analyze_axes(evidence_by_axis)
        agents_ms = int((perf_counter() - agents_started) * 1000)
        steps.append(
            ExecutionStep(
                name=f"{len(plan.enabled_axes)}専門エージェント（並列）",
                status="fallback" if used_fallback else "completed",
                elapsed_ms=agents_ms,
                detail=detail,
            )
        )
        await notify(
            f"{len(plan.enabled_axes)}専門エージェントの分析が完了しました"
            f"（{agents_ms} ms）"
        )

        axis_results = [
            score_axis(evidence_by_axis[axis], narratives[axis])
            for axis in plan.enabled_axes
        ]
        overall_score, overall_confidence = score_overall(plan, axis_results)

        evaluator_started = perf_counter()
        final_narrative, evaluator_fallback, evaluator_detail = (
            await self.team.create_final_narrative(
                region_name=region.name,
                overall_score=overall_score,
                axis_results=axis_results,
                excluded_axes=plan.excluded_axes,
            )
        )
        evaluator_ms = int((perf_counter() - evaluator_started) * 1000)
        steps.append(
            ExecutionStep(
                name="総合評価エージェント",
                status="fallback" if evaluator_fallback else "completed",
                elapsed_ms=evaluator_ms,
                detail=evaluator_detail,
            )
        )

        total_elapsed_ms = int((perf_counter() - started) * 1000)
        report = AssessmentReport(
            report_id=str(uuid4()),
            generated_at=datetime.now().astimezone(),
            plan=plan,
            overall_score=overall_score,
            overall_confidence=overall_confidence,
            axis_results=axis_results,
            narrative=final_narrative,
            execution_steps=steps,
            total_elapsed_ms=total_elapsed_ms,
            disclaimers=self._disclaimers(plan.excluded_axes),
        )
        markdown_path, json_path, markdown = self.writer.write(report)
        await notify(
            f"レポート保存完了: {markdown_path.name} / {json_path.name}"
            f"（全体 {total_elapsed_ms} ms）"
        )
        return report, markdown

    async def _fetch_all_axes(
        self,
        region: RegionInfo,
        axes: Sequence[Axis],
        notify: ProgressCallback,
    ) -> dict[Axis, AxisEvidence]:
        async def fetch(axis: Axis) -> tuple[Axis, AxisEvidence]:
            evidence = await asyncio.wait_for(
                self.provider.fetch_axis(region, axis),
                timeout=self.settings.data_timeout_seconds,
            )
            if (
                evidence.axis != axis
                or evidence.region.municipality_code != region.municipality_code
            ):
                raise ValueError(f"Evidence identity mismatch for axis={axis.value}")
            if evidence.data_mode != self.settings.data_mode:
                raise ValueError(f"Evidence data mode mismatch for axis={axis.value}")
            if evidence.data_mode == "government_api" and any(m.is_mock for m in evidence.metrics):
                raise ValueError("Live assessment cannot contain mock metrics")
            if not any(m.quality > 0 and m.direction != "context_only" for m in evidence.metrics):
                raise ValueError(f"No scorable evidence for axis={axis.value}")
            return axis, evidence

        tasks = [asyncio.create_task(fetch(axis)) for axis in axes]
        results: dict[Axis, AxisEvidence] = {}
        try:
            for completed in asyncio.as_completed(tasks):
                axis, evidence = await completed
                results[axis] = evidence
                await notify(
                    f"{AXIS_LABELS[axis]}データ取得完了: "
                    f"{len(evidence.api_calls)} API（{evidence.elapsed_ms} ms）"
                )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return results

    def _disclaimers(self, excluded_axes: Sequence[Axis]) -> list[str]:
        items = [
            "総合点と各軸点はPoC独自の相対評価であり、政府・自治体の公式評価ではありません。",
            "市区町村平均のため、町丁目・駅勢圏ごとの地域差は表しません。",
            "施設数は定員、空き、品質を保証しません。将来人口は推計であり予測の確実性を保証しません。",
            "公開APIの商用利用条件、出典表示、保存条件は本番化前に法務・セキュリティ確認が必要です。",
            "Google系APIは使用していません。",
        ]
        if self.settings.data_mode == "mock":
            items.insert(
                0,
                "すべての指標値はデモ用に生成したモックで、"
                "実在地域の実測値ではありません。",
            )
        if excluded_axes:
            items.insert(
                1,
                "除外した評価軸はAPI取得・専門エージェント分析・総合点の対象外です。",
            )
        return items

    async def close(self) -> None:
        await asyncio.gather(self.provider.close(), self.team.close())
