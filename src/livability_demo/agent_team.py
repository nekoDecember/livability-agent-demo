from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Never

from agent_framework import (
    Agent,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Executor,
    Message,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework.openai import OpenAIChatClient

from .catalog import AXIS_AGENT_NAMES
from .config import Settings
from .deterministic_analysis import build_axis_narrative, build_final_narrative
from .models import (
    AXIS_LABELS,
    Axis,
    AxisEvidence,
    AxisNarrative,
    AxisResult,
    FinalNarrative,
)
from .offline_client import PAYLOAD_MARKER, OfflineChatClient

SPECIALIST_INSTRUCTIONS = """
あなたは日本の地域住みやすさを評価する専門エージェントです。
SPECIALIST_AXIS={axis}

規則:
- 与えられた構造化エビデンスだけを使う。知識で数値を補わない。
- normalized_score は比較対象内の相対スコアであり、公式評価ではない。
- 強みと注意点をそれぞれ1～3件、非専門家にも分かる日本語で返す。
- モック値には必ずモックである旨を注意点に含める。
- 指定された AxisNarrative スキーマだけを返す。
""".strip()


FINAL_EVALUATOR_INSTRUCTIONS = """
あなたは総合評価・比較エージェントです。
FINAL_EVALUATOR

規則:
- コードで計算済みの点数を変更・再計算しない。
- 与えられた軸結果だけを根拠に、経理・営業を含む非専門家向けに要約する。
- 実行された専門エージェント数を5と決めつけない。除外軸を評価済みのように扱わない。
- モックデータを実データのように表現しない。
- 指定された FinalNarrative スキーマだけを返す。
""".strip()


class SpecialistPromptDispatcher(Executor):
    """Broadcast one assessment prompt to every specialist in the workflow graph."""

    @handler
    async def dispatch(
        self,
        prompt: str,
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        request = AgentExecutorRequest(
            messages=[Message("user", [prompt])],
            should_respond=True,
        )
        await ctx.send_message(request)


class SpecialistResultAggregator(Executor):
    """Validate the selected structured specialist replies at the fan-in boundary."""

    def __init__(self, expected_axes: Sequence[Axis]) -> None:
        self._expected_axes = frozenset(expected_axes)
        super().__init__(id="SpecialistResultAggregator")

    @handler
    async def aggregate(
        self,
        responses: list[AgentExecutorResponse],
        ctx: WorkflowContext[Never, dict[Axis, AxisNarrative]],
    ) -> None:
        narratives: dict[Axis, AxisNarrative] = {}
        for response in responses:
            value = response.agent_response.value
            narrative = (
                value
                if isinstance(value, AxisNarrative)
                else AxisNarrative.model_validate_json(response.agent_response.text)
            )
            if narrative.axis in narratives:
                raise RuntimeError(f"Duplicate specialist result: {narrative.axis.value}")
            narratives[narrative.axis] = narrative

        unexpected = set(narratives) - self._expected_axes
        if unexpected:
            raise RuntimeError(
                "Specialist workflow returned an unselected axis: "
                + ", ".join(sorted(axis.value for axis in unexpected))
            )
        missing = self._expected_axes - set(narratives)
        if missing:
            raise RuntimeError(
                "Specialist workflow did not return: "
                + ", ".join(sorted(axis.value for axis in missing))
            )
        await ctx.yield_output(narratives)


class AgentTeam:
    """Five specialists in an explicit graph workflow plus one final evaluator."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = self._build_client(settings)
        self.specialists: dict[Axis, Agent[Any]] = {
            axis: Agent(
                name=AXIS_AGENT_NAMES[axis],
                description=f"{AXIS_LABELS[axis]}を評価する専門エージェント",
                client=self._client,
                instructions=SPECIALIST_INSTRUCTIONS.format(axis=axis.value),
                default_options={"response_format": AxisNarrative},
            )
            for axis in Axis
        }
        self.evaluator = Agent(
            name="OverallEvaluationAgent",
            description="選択された軸の証拠と決定論的スコアを統合するエージェント",
            client=self._client,
            instructions=FINAL_EVALUATOR_INSTRUCTIONS,
            default_options={"response_format": FinalNarrative},
        )

    def build_specialist_workflow(self, enabled_axes: Sequence[Axis]) -> Workflow:
        """Create an inspectable graph containing only the selected specialist Agents."""

        requested = [Axis(axis) for axis in enabled_axes]
        selected = [axis for axis in Axis if axis in requested]
        if not selected:
            raise ValueError("At least one specialist Agent must be enabled.")
        if len(requested) != len(set(requested)):
            raise ValueError("Enabled specialist axes cannot contain duplicates.")

        dispatcher = SpecialistPromptDispatcher(id="SpecialistPromptDispatcher")
        aggregator = SpecialistResultAggregator(selected)
        specialist_agents = [self.specialists[axis] for axis in selected]
        return (
            WorkflowBuilder(
                start_executor=dispatcher,
                name="LivabilitySpecialistWorkflow",
                description=(
                    f"選択された{len(selected)}つの住みやすさ専門Agentを"
                    "並列実行して構造化結果を集約する"
                ),
                output_from=[aggregator],
            )
            .add_fan_out_edges(dispatcher, specialist_agents)
            .add_fan_in_edges(specialist_agents, aggregator)
            .build()
        )

    @staticmethod
    def _build_client(settings: Settings) -> Any:
        if settings.resolved_llm_mode == "openai":
            assert settings.openai_api_key is not None
            return OpenAIChatClient(
                model=settings.openai_model,
                api_key=settings.openai_api_key.get_secret_value(),
            )
        return OfflineChatClient()

    async def analyze_axes(
        self,
        evidence_by_axis: dict[Axis, AxisEvidence],
    ) -> tuple[dict[Axis, AxisNarrative], bool, str]:
        enabled_axes = [axis for axis in Axis if axis in evidence_by_axis]
        if not enabled_axes:
            raise ValueError("At least one axis of evidence is required.")
        payload = {
            "evidence_by_axis": {
                axis.value: evidence.model_dump(mode="json")
                for axis, evidence in evidence_by_axis.items()
            }
        }
        prompt = (
            "選択された専門担当が、各自のSPECIALIST_AXISに該当する証拠だけを"
            "分析してください。\n"
            f"{PAYLOAD_MARKER}\n{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            workflow = self.build_specialist_workflow(enabled_axes)
            events = await workflow.run(prompt)
            outputs = events.get_outputs()
            if len(outputs) != 1 or not isinstance(outputs[0], dict):
                raise RuntimeError("Specialist workflow returned an unexpected output.")

            narratives = outputs[0]
            return (
                narratives,
                False,
                "WorkflowBuilderのfan-out/fan-inで"
                f"{len(enabled_axes)}専門エージェントを並列実行: "
                + "、".join(AXIS_LABELS[axis] for axis in enabled_axes),
            )
        except Exception as exc:
            narratives = {
                axis: build_axis_narrative(evidence)
                for axis, evidence in evidence_by_axis.items()
            }
            return narratives, True, f"決定論的フォールバックを使用: {type(exc).__name__}: {exc}"

    async def create_final_narrative(
        self,
        *,
        region_name: str,
        overall_score: float,
        axis_results: list[AxisResult],
        excluded_axes: Sequence[Axis] = (),
    ) -> tuple[FinalNarrative, bool, str]:
        payload = {
            "region_name": region_name,
            "overall_score": overall_score,
            "axis_results": [result.model_dump(mode="json") for result in axis_results],
            "excluded_axes": [axis.value for axis in excluded_axes],
        }
        prompt = (
            "計算済みの評価結果を経営報告向けに要約してください。\n"
            f"{PAYLOAD_MARKER}\n{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            response = await self.evaluator.run(
                prompt,
                options={"response_format": FinalNarrative},
            )
            value = response.value
            if isinstance(value, FinalNarrative):
                return value, False, "総合評価エージェントが要約"
            parsed = FinalNarrative.model_validate_json(response.text)
            return parsed, False, "総合評価エージェントが要約"
        except Exception as exc:
            return (
                build_final_narrative(region_name, overall_score, axis_results),
                True,
                f"決定論的フォールバックを使用: {type(exc).__name__}: {exc}",
            )

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
