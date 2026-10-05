from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from time import perf_counter
from typing import Literal
from uuid import uuid4

from .agent_team import AgentTeam
from .config import Settings
from .data_providers import (
    RegionalDataProvider,
    build_data_provider,
    resolve_region_without_external_data,
)
from .deterministic_analysis import build_candidate_narrative
from .models import (
    AXIS_LABELS,
    ApiCallTrace,
    AssessmentReport,
    Axis,
    AxisEvidence,
    AxisResult,
    CandidateComparison,
    ExecutionStep,
    MetricEvidence,
    ResearchContext,
    ResearchSource,
    SourceReference,
)
from .planning import build_plan
from .report_writer import ReportWriter
from .scoring import calculate_research_confidence, score_axis

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
        weights: dict[Axis, float] | None = None,
        mode: Literal["data", "knowledge_only"] = "data",
    ) -> tuple[AssessmentReport, str]:
        if mode == "knowledge_only":
            return await self._assess_knowledge_only(
                user_request,
                progress=progress,
                enabled_axes=enabled_axes,
                weights=weights,
            )

        notify = progress or _noop_progress
        started = perf_counter()
        steps: list[ExecutionStep] = []
        configured_axes = tuple(enabled_axes) if enabled_axes is not None else None

        plan_started = perf_counter()
        region = await asyncio.wait_for(
            self.provider.resolve_region(user_request),
            timeout=self.settings.data_timeout_seconds,
        )
        initial_plan = build_plan(
            user_request,
            region,
            data_mode=self.settings.data_mode,
            enabled_axes=configured_axes,
            requested_weights=weights,
        )
        available_axes = set(
            await asyncio.wait_for(
                self.provider.available_axes(region),
                timeout=self.settings.data_timeout_seconds,
            )
        )
        data_unavailable_axes = [
            axis for axis in initial_plan.enabled_axes if axis not in available_axes
        ]
        unavailable_reasons = {
            axis: await self.provider.axis_unavailability_reason(region, axis)
            for axis in data_unavailable_axes
        }
        if data_unavailable_axes:
            usable_axes = [axis for axis in initial_plan.enabled_axes if axis in available_axes]
            if not usable_axes:
                raise ValueError(f"{region.name}には評価可能な公式公開データ軸がありません。")
            usable_weights = (
                {axis: value for axis, value in weights.items() if axis in usable_axes}
                if weights
                else None
            )
            plan = build_plan(
                user_request,
                region,
                data_mode=self.settings.data_mode,
                enabled_axes=usable_axes,
                requested_weights=usable_weights
                if usable_weights and sum(usable_weights.values()) > 0
                else None,
            )
            unavailable_labels = "、".join(AXIS_LABELS[axis] for axis in data_unavailable_axes)
            plan = plan.model_copy(
                update={
                    "unavailable_axes": data_unavailable_axes,
                    "unavailable_axis_reasons": unavailable_reasons,
                    "agent_selection_reason": (
                        f"{plan.agent_selection_reason}。評価できない軸を除外: {unavailable_labels}"
                    ),
                }
            )
        else:
            plan = initial_plan
        plan_ms = int((perf_counter() - plan_started) * 1000)
        steps.append(
            ExecutionStep(
                name="受付・計画エージェント",
                status="completed",
                elapsed_ms=plan_ms,
                detail=(f"{region.name} / {plan.agent_selection_reason} / {plan.weight_reason}"),
            )
        )
        await notify(
            f"受付・計画: {region.name}を特定し、{plan.agent_selection_reason}（{plan_ms} ms）"
        )

        research_started = perf_counter()
        data_source_label = (
            "公式公開データスナップショット"
            if self.settings.data_mode == "open_data"
            else "地域データAPI"
        )
        await notify(
            f"{len(plan.enabled_axes)}軸の担当エージェントが{data_source_label}を個別に収集し、"
            "根拠を分析します"
        )
        evidence_by_axis, narratives, used_fallback, detail = await self.team.research_axes(
            region=region,
            provider=self.provider,
            enabled_axes=plan.enabled_axes,
            user_request=user_request,
            progress=notify,
        )
        research_ms = int((perf_counter() - research_started) * 1000)
        data_elapsed = max(evidence.elapsed_ms for evidence in evidence_by_axis.values())
        await notify(
            f"{len(plan.enabled_axes)}軸の担当エージェントが情報収集と分析を完了しました"
            f"（{research_ms} ms）"
        )
        for axis in plan.enabled_axes:
            evidence = evidence_by_axis[axis]
            endpoints = ", ".join(call.endpoint for call in evidence.api_calls)
            steps.append(
                ExecutionStep(
                    name=(
                        f"{AXIS_LABELS[axis]}公式公開データ"
                        if self.settings.data_mode == "open_data"
                        else f"{AXIS_LABELS[axis]}データAPI"
                    ),
                    status="completed",
                    elapsed_ms=evidence.elapsed_ms,
                    detail=endpoints,
                )
            )
        api_count = sum(len(item.api_calls) for item in evidence_by_axis.values())
        steps.append(
            ExecutionStep(
                name=(
                    "公式公開データ群"
                    if self.settings.data_mode == "open_data"
                    else "地域データAPI群"
                ),
                status="completed",
                elapsed_ms=data_elapsed,
                detail=(
                    f"{len(plan.enabled_axes)}領域・{api_count} "
                    + (
                        "担当Agentが並列参照"
                        if self.settings.data_mode == "open_data"
                        else "担当AgentがAPIツールを並列参照"
                    )
                ),
            )
        )
        for axis in plan.excluded_axes:
            unavailable = axis in data_unavailable_axes
            steps.append(
                ExecutionStep(
                    name=(
                        f"{AXIS_LABELS[axis]}公式公開データ・専門エージェント"
                        if self.settings.data_mode == "open_data"
                        else f"{AXIS_LABELS[axis]}データAPI・専門エージェント"
                    ),
                    status="skipped",
                    elapsed_ms=0,
                    detail=(
                        "この軸の値を照合できず、分析・採点から除外"
                        if unavailable_reasons.get(axis) == "unverified"
                        else "この自治体の値が今回のデータ集になく、分析・採点から除外"
                        if unavailable_reasons.get(axis) == "not_found_for_region"
                        else "今回のデータ集にこの軸の指標がなく、分析・採点から除外"
                        if unavailable
                        else "Agent構成指定によりAPI取得・分析・採点を未実行"
                    ),
                )
            )

        is_parallel = len(plan.enabled_axes) > 1
        steps.append(
            ExecutionStep(
                name=(
                    f"{len(plan.enabled_axes)}専門エージェント（並列）"
                    if is_parallel
                    else "1専門エージェント（単独）"
                ),
                status="fallback" if used_fallback else "completed",
                elapsed_ms=research_ms,
                detail=detail,
            )
        )

        from .deterministic_analysis import build_axis_summary

        axis_results = [
            score_axis(
                evidence_by_axis[axis],
                narratives[axis].model_copy(
                    update={"summary": build_axis_summary(evidence_by_axis[axis], user_request)}
                ),
            )
            for axis in plan.enabled_axes
        ]
        research_confidence = calculate_research_confidence(plan, axis_results)
        candidate_narrative = build_candidate_narrative(
            region_name=region.name,
            results=axis_results,
            user_request=user_request,
            weights=plan.weights,
        )
        steps.append(
            ExecutionStep(
                name="候補別の根拠整理",
                status="completed",
                elapsed_ms=0,
                detail="候補単体の所見を記録。比較提案はコマンダーが全候補の調査後に作成",
            )
        )

        total_elapsed_ms = int((perf_counter() - started) * 1000)
        report = AssessmentReport(
            report_id=str(uuid4()),
            generated_at=datetime.now().astimezone(),
            plan=plan,
            research_confidence=research_confidence,
            axis_results=axis_results,
            narrative=candidate_narrative,
            execution_steps=steps,
            total_elapsed_ms=total_elapsed_ms,
            disclaimers=self._disclaimers(
                plan.excluded_axes,
                unavailable_axes=data_unavailable_axes,
            ),
            research_context=ResearchContext(
                method="mock" if self.settings.data_mode == "mock" else "data_context",
                status="offline" if self.settings.data_mode == "mock" else "verified",
                controlled_fields=[
                    "候補都市と自治体コード",
                    "暮らしの条件と比較する視点",
                    "指標の定義・単位・対象年・出典",
                    "比較できる指標と欠損の扱い",
                    "取得データを各担当に渡す範囲",
                ],
                limitations=[
                    "指定したすべての指標が取得できるとは限りません。未取得は補完しません。",
                    "確認済みなのはデータの来歴と形式です。回答の正しさや生活上の成果を保証しません。",
                    "公式公開データモードでは、API・配布ファイルから事前同期した版を使います。",
                ],
                sources=list(
                    {
                        metric.source.url: ResearchSource(
                            url=metric.source.url, title=metric.source.source_name
                        )
                        for result in axis_results
                        for metric in result.metrics
                        if metric.quality > 0 and metric.source.url
                    }.values()
                ),
            ),
        )
        markdown_path, json_path, markdown = self.writer.write(report)
        await notify(
            f"レポート保存完了: {markdown_path.name} / {json_path.name}"
            f"（全体 {total_elapsed_ms} ms）"
        )
        return report, markdown

    async def compare_candidates(
        self,
        reports: Sequence[AssessmentReport],
        *,
        user_request: str,
        weights: dict[Axis, float] | None = None,
    ) -> CandidateComparison:
        return await self.team.compare_candidates(
            reports=reports,
            user_request=user_request,
            weights=weights or {axis: 20.0 for axis in Axis},
        )

    async def _assess_knowledge_only(
        self,
        user_request: str,
        *,
        progress: ProgressCallback | None = None,
        enabled_axes: Iterable[Axis] | None = None,
        weights: dict[Axis, float] | None = None,
    ) -> tuple[AssessmentReport, str]:
        """Run a no-regional-API assessment using the LLM's general knowledge only."""

        notify = progress or _noop_progress
        started = perf_counter()
        steps: list[ExecutionStep] = []

        plan_started = perf_counter()
        region = resolve_region_without_external_data(user_request)
        plan = build_plan(
            user_request,
            region,
            data_mode="knowledge_only",
            enabled_axes=enabled_axes,
            requested_weights=weights,
        )
        plan_ms = int((perf_counter() - plan_started) * 1000)
        steps.append(
            ExecutionStep(
                name="受付・計画エージェント",
                status="completed",
                elapsed_ms=plan_ms,
                detail=f"{region.name} / {plan.agent_selection_reason} / LLM知識のみ",
            )
        )
        await notify(
            f"受付・計画: {region.name}を特定しました。"
            "外部データAPIは使わず、LLM知識のみで評価します"
        )

        for axis in plan.enabled_axes:
            steps.append(
                ExecutionStep(
                    name=f"{AXIS_LABELS[axis]}データAPI",
                    status="skipped",
                    elapsed_ms=0,
                    detail="LLM知識のみモードのため外部データAPIを呼び出していません",
                )
            )
        steps.append(
            ExecutionStep(
                name="地域データAPI群",
                status="skipped",
                elapsed_ms=0,
                detail="外部データAPI・検索・地域データツールは未使用",
            )
        )
        await notify("地域データAPI群をスキップしました（外部データなし）")

        evaluator_started = perf_counter()
        await notify("LLM知識のみ評価を開始しました（最新統計の取得なし）")
        (
            knowledge_assessment,
            used_fallback,
            detail,
        ) = await self.team.create_knowledge_only_assessment(
            user_request=user_request,
            region_name=region.name,
            enabled_axes=plan.enabled_axes,
        )
        evaluator_ms = int((perf_counter() - evaluator_started) * 1000)
        expected_axes = list(plan.enabled_axes)
        by_axis = {item.axis: item for item in knowledge_assessment.axis_assessments}
        if list(by_axis) != expected_axes:
            raise ValueError("Knowledge-only assessment returned an incomplete axis set")

        source = SourceReference(
            source_id="LLM-KNOWLEDGE",
            source_name="LLM内在知識（外部データ未参照）",
            endpoint="not-called",
            url="",
            reference_date="知識カットオフ時点・最新性保証なし",
            commercial_use_note="外部統計の出典ではありません。意思決定前に一次情報を確認してください。",
        )
        axis_results: list[AxisResult] = []
        for axis in expected_axes:
            item = by_axis[axis]
            evidence = AxisEvidence(
                axis=axis,
                region=region,
                metrics=[
                    MetricEvidence(
                        metric_code=f"llm_knowledge_{axis.value}",
                        label="LLMによる条件適合度（外部データなし）",
                        value=item.score,
                        unit="推定スコア / 100",
                        normalized_score=item.score,
                        direction="higher_is_better",
                        weight=1.0,
                        source=source,
                        quality=item.confidence,
                        note="測定値ではなく、LLMの一般知識だけによる比較用の仮説",
                        is_mock=False,
                    )
                ],
                api_calls=[
                    ApiCallTrace(
                        tool_name="regional_data_apis",
                        endpoint="not-called",
                        source_id=source.source_id,
                        status="skipped",
                        elapsed_ms=0,
                    )
                ],
                elapsed_ms=0,
                data_mode="knowledge_only",
            )
            axis_results.append(score_axis(evidence, item.narrative))

        steps.append(
            ExecutionStep(
                name=f"{len(expected_axes)}軸・LLM知識のみ評価",
                status="fallback" if used_fallback else "completed",
                elapsed_ms=evaluator_ms,
                detail=detail,
            )
        )
        research_confidence = calculate_research_confidence(plan, axis_results)
        total_elapsed_ms = int((perf_counter() - started) * 1000)
        report = AssessmentReport(
            report_id=str(uuid4()),
            generated_at=datetime.now().astimezone(),
            plan=plan,
            research_confidence=research_confidence,
            axis_results=axis_results,
            narrative=knowledge_assessment.narrative,
            execution_steps=steps,
            total_elapsed_ms=total_elapsed_ms,
            disclaimers=self._disclaimers(plan.excluded_axes, data_mode="knowledge_only"),
            research_context=ResearchContext(
                method="knowledge_only",
                status="offline",
                limitations=["旧モードの内在知識による回答です。Web検索は実行していません。"],
            ),
        )
        markdown_path, json_path, markdown = self.writer.write(report)
        await notify(
            f"LLM知識のみのレポート保存完了: {markdown_path.name} / {json_path.name}"
            f"（全体 {total_elapsed_ms} ms）"
        )
        return report, markdown

    def _disclaimers(
        self,
        excluded_axes: Sequence[Axis],
        *,
        data_mode: str | None = None,
        unavailable_axes: Sequence[Axis] = (),
    ) -> list[str]:
        active_data_mode = data_mode or self.settings.data_mode
        usage_terms_note = (
            "公式公開データの版ごとの利用条件、出典表示、第三者権利は"
            "スナップショット更新時にも再確認してください。"
            if active_data_mode == "open_data"
            else "公開APIの商用利用条件、出典表示、保存条件は本番化前に"
            "法務・セキュリティ確認が必要です。"
        )
        items = [
            "総合点と各軸点は共通の指標範囲で計算した独自評価であり、政府・自治体の公式評価ではありません。",
            "市区町村平均のため、町丁目・駅勢圏ごとの地域差は表しません。",
            "施設数は定員、空き、品質を保証しません。将来人口は推計であり予測の確実性を保証しません。",
            usage_terms_note,
            "Google系APIは使用していません。",
        ]
        if active_data_mode == "mock":
            items.insert(
                0,
                "すべての指標値はデモ用に生成したモックで、実在地域の実測値ではありません。",
            )
        if active_data_mode == "open_data":
            items.insert(
                0,
                "実行時の契約API・HTMLスクレイピングは使わず、"
                "事前検証済みの公式公開データスナップショットだけを参照しています。",
            )
            items.insert(
                1,
                "スナップショットにない指標は推測せず採点対象外とし、"
                "欠損分は信頼度へ反映しています。",
            )
        if unavailable_axes:
            labels = "、".join(AXIS_LABELS[axis] for axis in unavailable_axes)
            items.insert(
                0,
                f"{labels}は対象地域で軸単位の値を確認できないため、"
                "総合点から除外して残りの軸へウェイトを再配分しています。",
            )
        if active_data_mode == "knowledge_only":
            items.insert(
                0,
                "外部データAPI・検索を使わず、LLMの一般知識だけで作った予備評価です。",
            )
            items.insert(
                1,
                "点数・強み・注意点は測定値ではなく、知識の時点とLLMの誤りに左右されます。",
            )
        if excluded_axes:
            items.insert(
                1,
                "除外した評価軸はAPI取得・専門エージェント分析・総合点の対象外です。",
            )
        return items

    async def close(self) -> None:
        await asyncio.gather(self.provider.close(), self.team.close())
