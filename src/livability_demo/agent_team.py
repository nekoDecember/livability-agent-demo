from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Never

from agent_framework import (
    Agent,
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework.openai import OpenAIChatClient
from pydantic import BaseModel, Field, ValidationError

from .catalog import AXIS_AGENT_NAMES
from .config import Settings
from .data_providers import RegionalDataProvider
from .deterministic_analysis import (
    build_axis_narrative,
    build_commander_narrative,
    build_knowledge_only_assessment,
    metric_life_meaning,
)
from .models import (
    AXIS_LABELS,
    AssessmentReport,
    Axis,
    AxisEvidence,
    AxisNarrative,
    CandidateComparison,
    CommanderNarrative,
    KnowledgeOnlyAssessment,
    RegionInfo,
)
from .offline_client import PAYLOAD_MARKER, OfflineChatClient
from .regional_context import context_metrics, requests_tertiary_education_context
from .sales_proposal import comparable_metric_codes, validate_sales_proposal

logger = logging.getLogger(__name__)


def _safe_commander_error_context(exc: Exception) -> str:
    """Log provider error codes without copying prompts or raw provider messages."""

    chain: list[BaseException] = [exc]
    for related in (
        getattr(exc, "inner_exception", None),
        getattr(exc, "__cause__", None),
        getattr(exc, "__context__", None),
    ):
        if isinstance(related, BaseException) and all(related is not item for item in chain):
            chain.append(related)

    details = [f"exception_type={type(exc).__name__}"]
    if len(chain) > 1:
        details.append(f"root_cause_type={type(chain[-1]).__name__}")
    for item in chain:
        status_code = getattr(item, "status_code", None)
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            details.append(f"status_code={status_code}")
            break
    for item in chain:
        error_code = getattr(item, "code", None)
        if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", error_code):
            details.append(f"error_code={error_code}")
            break
    return " ".join(details)


SPECIALIST_INSTRUCTIONS = (
    "\n"
    "あなたは日本の地域住みやすさを評価する専門エージェントです。\n"
    "SPECIALIST_AXIS={axis}\n"
    "\n"
    "規則:\n"
    "- 与えられた構造化エビデンスだけを使う。知識で数値を補わない。\n"
    "- normalized_score は共通の指標範囲に対する0〜100点であり"
    "、候補内順位でも公式評価でもない。\n"
    "- quality=0 の指標は未取得。value=0を実測値として扱わず、強み"
    "・弱みの判断に使わない。\n"
    "- 強みと注意点をそれぞれ1～3件、非専門家にも分かる日本語で返す。\n"
    "- 指標名をそのまま繰り返すのではなく、住む人の生活で何が変わるかに置き換えて説"
    "明する。数値は意味を伝えた後に根拠として短く添える。\n"
    "- データが取得できているときは、その差が暮らしにどう効くかを端的に述べる。「判"
    "断材料として不十分」などの定型的な逃げを繰り返さない。\n"
    "- 未取得条件への注意は行動に関わる場合だけ短く述べ、同じ注意を強み・注意点で繰"
    "り返さない。駅までの距離、運行本数、保育の空き、住居費などの値そのものは作らない"
    "。\n"
    "- context_only の地域固有情報は採点せず、出典を踏まえた説明にのみ"
    "使う。\n"
    "- モック値には必ずモックである旨を注意点に含める。\n"
    "- 指定された AxisNarrative スキーマだけを返す。\n"
).strip()


COMMANDER_COMPARISON_INSTRUCTIONS = (
    "\n"
    "あなたはLivabilityのコマンダーです。候補地ごとの専門エージェントの調査"
    "結果を、利用者の条件に合わせて統合します。\n"
    "COMMANDER_COMPARISON\n"
    "\n"
    "規則:\n"
    "- あなたは都市選びの営業担当の役割を担う。利用者の希望を受け、最終的にどこを選"
    "ぶべきかを総合提案し、他候補との違いと次の行動まで説明する。\n"
    "- お客さまにそのまま読んでもらう提案として書く。「取得値」「正規化スコア」「人"
    "口当たりの供給密度」などの用語は、日常の場面に翻訳する。たとえば「普段の受診先を"
    "探す」「子どもの預け先を探す」「土地分の購入予算を考える」と希望に結び付ける。\n"
    "- metric_codeの英語コードはsupporting_metric_co"
    "desだけに出力し、summary・reasons・tradeoffs・next"
    "_checks・candidate_positionsなどのお客さま向け文章には"
    "書かない。\n"
    "- 各理由は「希望に合う暮らしの場面 → どちらの街にどんな根拠があるか」の順に"
    "書く。根拠で確認できた事実は明確に述べる。未確認の物件ごとの体験を約束しない。\n"
    "- proposal_titleは希望と推奨都市を結び付けた短い提案見出し。su"
    "mmaryは結論から始め、なぜその希望でその都市を選ぶかを説明する。\n"
    "- recommendation_strengthは、希望に直接関係する比較可能"
    "な実データに明確な差があり重要な未評価条件が結論を左右しない場合recommen"
    "ded、未評価の重要条件がある場合conditional、知識のみ・モックの場合"
    "hypothesis、一つに選び分けられない場合undecidedにする。実デー"
    "タがあるだけでrecommendedにしない。\n"
    "- recommendedなら「今回の希望には○市をおすすめします」と明確に述べ"
    "る。conditionalなら「○○を重視する今回の条件では○市を第一候補にしま"
    "す」と適用条件を示す。\n"
    "- supporting_metric_codesはcomparable_met"
    "ric_codesの中から決め手に使ったコードを選ぶ。\n"
    "  実データの推薦では1〜6件、知識のみ・モックでは空配列にする。推定スコアを実"
    "測の根拠にしない。\n"
    "- supporting_metric_codesは、希望に直接結び付く決め手か"
    "ら重要な順に並べる。土地購入の予算を重視する希望に対して住宅数を決め手に加えない"
    "。直接関係する比較だけで理由が揃う場合、間接的な指標で理由を水増ししない。\n"
    "- candidate_positionsには全候補を一度ずつ含める。fit_s"
    "ummaryは希望への適合とその根拠、selection_conditionは「"
    "どの希望・条件ならその都市を選ぶか」。推薦しなかった都市についても選ぶ条件を具体"
    "的に説明し、取得していない地域事情を作らない。\n"
    "- 事実の比較は「多い」「低い」と言い切り、暮らしへの解釈は指標の適用範囲を添え"
    "る。実測値の差が小さい場合は、その指標を選択の決め手にしないことも成果として説明"
    "する。\n"
    "- 全指標の取得率が低いことだけを理由に、根拠が揃った個別比較まで曖昧にしない。"
    "\n"
    "- 各候補の全軸について、専門家のsummary・strengths・cauti"
    "onsと取得状況を読んでから統合する。\n"
    "- 利用者の暮らしの条件と重視割合を判断の主語にし、最初に検討する候補をreco"
    "mmended_region_codeへ返す。候補コードは入力にあるものだけを使"
    "い、十分な根拠がなければnullにする。\n"
    "- 軸スコアの単純合計だけで候補を選ばない。割合は利用者が何を重視するかを表す手"
    "掛かりとして使い、条件と各候補の具体的な証拠・トレードオフを総合して判断する。\n"
    "- summaryは、誰のどの暮らしにどの候補が合いそうかを、提案の強さも含め短"
    "く説明する。総合点や「○点だから選ぶ」という説明にしない。\n"
    "- reasonsは暮らしへの意味を先に述べ、取得済み指標の数値・単位・年を括弧"
    "内などに短く添える。指標名や数値の羅列だけにしない。利用者の条件に関係する指標を"
    "優先し、各理由は2文以内にする。\n"
    "- 「要件を満たしていない」「～とは言えない」「判断できない」といった評価書調の"
    "注釈を理由に並べない。必要な確認事項はnext_checksへ一度だけ移す。\n"
    "- 出典メモ(note)をそのまま注意書きとして貼り付けない。意味を誤解させる恐"
    "れがある場合だけ、利用者の条件に直接関わる短い説明に言い換える。\n"
    "- 土地価格は「土地を買って家を建てる場合の土地取得費」として説明する。家賃や建"
    "物の建築費まで含むようには言わない。\n"
    "- 住宅数は市全体の住まいの量であり、募集中の物件数・空室率・家賃の安さではない"
    "。住宅数の多さを「住まいコストが安い」という推薦理由にしない。保育施設数から入園"
    "可能性、診療所数から近さや待ち時間、人口推計から資産価値や将来のサービス維持を断"
    "定しない。\n"
    "- tradeoffsは、利用者が重視する点について他候補が勝る場合に、その暮ら"
    "し上の違いを書く。一般的な免責や理由と同じ注意を繰り返さない。\n"
    "- next_checksには未測定条件を利用者が次に確認できる具体的行動として"
    "まとめ、理由で触れたデータの限界はここで一度だけ補足する。\n"
    "- next_checksは、お客さまが次に何をすれば決められるかを書く。たとえ"
    "ば「希望する地区の売地を同じ面積で比較する」「必要な診療科を確認して候補の家から"
    "の経路を調べる」。抽象的な「追加データの取得」だけで終わらせない。\n"
    "- next_checksは今回の希望に関係する行動に絞る。利用者が子育てを希望"
    "していない場合に保育の空きを確認させるなど、別の生活条件を勝手に加えない。sum"
    "maryは2文以内で結論と主要な決め手を伝え、詳細はreasonsとnext_c"
    "hecksに置く。\n"
    "- 指標の翻訳例: 「人口10万人あたり診療所101.5施設」なら「市全体では、"
    "ふだんの受診先を選ぶ余地が比較的大きそうです（人口10万人あたり101.5施設）"
    "」。「住宅地の地価が高い」なら「土地を買って家を建てる場合、土地分の予算が大きく"
    "なりやすい傾向です」。\n"
    "- 通勤先と候補自治体が同じというだけで、所要時間が短いと結論しない。家賃・渋滞"
    "・施設の空き・町丁目差を作らない。\n"
    "- 品質0の指標、未取得の情報、背景情報は得点の根拠として使わない。\n"
    "- 入力にない地域事情・施設・路線・地名・数値を追加しない。\n"
    "- 条件に結び付くデータが少ない・候補間で差が小さい場合は、summaryで暫定"
    "提案または選び分け困難と明示する。\n"
    "- summary・reasons・tradeoffs・next_checksの"
    "全項目を自然な日本語で書く。日本語の漢字・ひらがな・カタカナと必要なラテン文字だ"
    "けを使い、他の文字体系を混ぜない。\n"
    "- 不可視の制御記号や、`[... EOL]`などの生成・編集用プレースホルダー"
    "を含めない。\n"
    "- 指定されたCommanderNarrativeスキーマだけを返す。\n"
).strip()


KNOWLEDGE_ONLY_INSTRUCTIONS = """
あなたは、外部の地域データAPI・検索・ツールを一切使わずに回答する、
日本の住みやすさ比較アドバイザーです。
KNOWLEDGE_ONLY

規則:
- あなたの学習済みの一般知識だけを使い、最新の統計値・施設数・価格・災害範囲を断定しない。
- 入力された候補地域に対して、指定された評価軸ごとに「その条件への適合度」の粗い0〜100点を付ける。
- 点数は測定値ではなく比較のための仮説。自信が低いときはconfidenceを低くする。
- 町丁目・駅・物件・時点による差は必ず注意点に含める。
- 結論は曖昧に逃げず、与えられた条件で何を第一候補にすべきか分かる日本語で書く。
- 指定されたKnowledgeOnlyAssessmentスキーマだけを返す。
""".strip()


class SpecialistInput(BaseModel):
    evidence_by_axis: dict[Axis, AxisEvidence] = Field(default_factory=dict)
    region: RegionInfo | None = None
    user_request: str = ""


class SpecialistOutcome(BaseModel):
    axis: Axis
    narrative: AxisNarrative
    evidence: AxisEvidence | None = None
    used_fallback: bool = False
    detail: str = ""


class SpecialistBatch(BaseModel):
    outcomes: list[SpecialistOutcome]


class SpecialistPromptDispatcher(Executor):
    """Route typed evidence to host executors; each model sees only its own axis."""

    @handler
    async def dispatch(
        self,
        data: SpecialistInput,
        ctx: WorkflowContext[SpecialistInput],
    ) -> None:
        await ctx.send_message(data)


class AxisSpecialistExecutor(Executor):
    """Validate and recover one branch without discarding successful siblings."""

    def __init__(
        self,
        axis: Axis,
        agent: Agent[Any],
        settings: Settings,
        *,
        provider: RegionalDataProvider | None = None,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.axis = axis
        self.agent = agent
        self.settings = settings
        self.provider = provider
        self.progress = progress
        super().__init__(id=AXIS_AGENT_NAMES[axis])

    @handler
    async def analyze(
        self,
        data: SpecialistInput,
        ctx: WorkflowContext[SpecialistOutcome],
    ) -> None:
        if self.provider is not None:
            if data.region is None:
                raise ValueError("Specialist research requires a resolved region.")
            evidence = await asyncio.wait_for(
                self.provider.fetch_axis(data.region, self.axis),
                timeout=self.settings.data_timeout_seconds,
            )
            if (
                evidence.axis != self.axis
                or evidence.region.municipality_code != data.region.municipality_code
            ):
                raise ValueError(f"Evidence identity mismatch for axis={self.axis.value}")
            if evidence.data_mode not in {"mock", "open_data", "government_api"}:
                raise ValueError(f"Unsupported provider data mode for axis={self.axis.value}")
            if evidence.data_mode != self.settings.data_mode:
                raise ValueError(f"Evidence data mode mismatch for axis={self.axis.value}")
            if evidence.data_mode in {"government_api", "open_data"} and any(
                metric.is_mock for metric in evidence.metrics
            ):
                raise ValueError("Live specialist research cannot contain mock metrics")
            if not any(
                metric.quality > 0 and metric.direction != "context_only"
                for metric in evidence.metrics
            ):
                raise ValueError(f"No scorable evidence for axis={self.axis.value}")
            facts = context_metrics(data.region, self.axis)
            if facts:
                evidence = evidence.model_copy(update={"metrics": [*evidence.metrics, *facts]})
            if self.axis == Axis.FAMILY and not requests_tertiary_education_context(
                data.user_request
            ):
                evidence = evidence.model_copy(
                    update={
                        "metrics": [
                            metric
                            for metric in evidence.metrics
                            if metric.metric_code != "tertiary_education_campuses"
                        ]
                    }
                )
            living_conditions = data.user_request.split("。条件: ", 1)[-1]
            if (
                self.axis == Axis.FAMILY
                and "独身" in living_conditions
                and not any(term in living_conditions for term in ("子育て", "子ども", "子供"))
            ):
                childcare_codes = {"nursery_per_1000_children", "schools_per_1000_children"}
                evidence = evidence.model_copy(
                    update={
                        "metrics": [
                            metric.model_copy(
                                update={
                                    "direction": "context_only",
                                    "note": ((metric.note + " / ") if metric.note else "")
                                    + (
                                        "独身の条件では採点対象外。将来の子育てを希望する場合は条件を変更。"
                                    ),
                                }
                            )
                            if metric.metric_code in childcare_codes
                            else metric
                            for metric in evidence.metrics
                        ]
                    }
                )
            if self.progress is not None:
                await self.progress(
                    f"{AXIS_LABELS[self.axis]}担当が{len(evidence.api_calls)}件のデータ参照を取得"
                )
        else:
            evidence = data.evidence_by_axis[self.axis]
        payload = {"evidence_by_axis": {self.axis.value: evidence.model_dump(mode="json")}}
        prompt = f"担当軸の証拠だけを分析してください。\n{PAYLOAD_MARKER}\n"
        prompt += json.dumps(payload, ensure_ascii=False)
        detail = ""
        for attempt in range(self.settings.specialist_attempts):
            try:
                response = await asyncio.wait_for(
                    self.agent.run(prompt, session=self.agent.create_session()),
                    timeout=self.settings.agent_timeout_seconds,
                )
                value = response.value
                narrative = (
                    value
                    if isinstance(value, AxisNarrative)
                    else AxisNarrative.model_validate_json(response.text)
                )
                if narrative.axis != self.axis:
                    raise ValueError(f"Expected specialist axis {self.axis.value}")
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < self.settings.specialist_attempts:
                    # This branch is read-only. A fresh session avoids retaining invalid output.
                    prompt = (
                        f"前回の出力は検証に失敗しました。担当軸 {self.axis.value} の"
                        f"AxisNarrativeだけを返してください。\n{PAYLOAD_MARKER}\n"
                        + json.dumps(payload, ensure_ascii=False)
                    )
            else:
                await ctx.send_message(
                    SpecialistOutcome(
                        axis=self.axis,
                        narrative=narrative,
                        evidence=evidence,
                    )
                )
                return
        await ctx.send_message(
            SpecialistOutcome(
                axis=self.axis,
                narrative=build_axis_narrative(evidence),
                evidence=evidence,
                used_fallback=True,
                detail=detail,
            )
        )


class SpecialistResultAggregator(Executor):
    """Validate the selected structured specialist replies at the fan-in boundary."""

    def __init__(self, expected_axes: Sequence[Axis]) -> None:
        self._expected_axes = frozenset(expected_axes)
        super().__init__(id="SpecialistResultAggregator")

    @handler
    async def aggregate(
        self,
        responses: list[SpecialistOutcome],
        ctx: WorkflowContext[Never, SpecialistBatch],
    ) -> None:
        narratives: dict[Axis, AxisNarrative] = {}
        for response in responses:
            narrative = response.narrative
            if response.axis != narrative.axis:
                raise RuntimeError("Specialist result does not match its assigned axis")
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
        await ctx.yield_output(SpecialistBatch(outcomes=responses))


class SingleSpecialistResultAggregator(Executor):
    """Adapt one specialist result without creating an invalid one-target fan-in."""

    def __init__(self, expected_axis: Axis) -> None:
        self._expected_axis = expected_axis
        super().__init__(id="SingleSpecialistResultAggregator")

    @handler
    async def aggregate(
        self,
        response: SpecialistOutcome,
        ctx: WorkflowContext[Never, SpecialistBatch],
    ) -> None:
        if response.axis != self._expected_axis:
            raise RuntimeError(f"Expected {self._expected_axis.value}, got {response.axis.value}")
        if response.axis != response.narrative.axis:
            raise RuntimeError("Specialist result does not match its assigned axis")
        await ctx.yield_output(SpecialistBatch(outcomes=[response]))


class AgentTeam:
    """Axis specialists and a cross-city commander."""

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
        self.commander = Agent(
            name="LivabilityCommanderAgent",
            description="候補地ごとの専門調査と利用者条件を統合し、暮らし方の提案を作る",
            client=self._client,
            instructions=COMMANDER_COMPARISON_INSTRUCTIONS,
            default_options={"response_format": CommanderNarrative},
        )
        self.knowledge_evaluator = Agent(
            name="KnowledgeOnlyEvaluationAgent",
            description="外部データを使わずLLMの一般知識だけで予備評価するエージェント",
            client=self._client,
            instructions=KNOWLEDGE_ONLY_INSTRUCTIONS,
            default_options={"response_format": KnowledgeOnlyAssessment},
        )

    def build_specialist_workflow(
        self,
        enabled_axes: Sequence[Axis],
        *,
        provider: RegionalDataProvider | None = None,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> Workflow:
        """Create an inspectable graph containing only the selected specialist Agents."""

        requested = [Axis(axis) for axis in enabled_axes]
        selected = [axis for axis in Axis if axis in requested]
        if not selected:
            raise ValueError("At least one specialist Agent must be enabled.")
        if len(requested) != len(set(requested)):
            raise ValueError("Enabled specialist axes cannot contain duplicates.")

        dispatcher = SpecialistPromptDispatcher(id="SpecialistPromptDispatcher")
        specialist_agents = [
            AxisSpecialistExecutor(
                axis,
                self.specialists[axis],
                self._settings,
                provider=provider,
                progress=progress,
            )
            for axis in selected
        ]
        if len(specialist_agents) == 1:
            aggregator = SingleSpecialistResultAggregator(selected[0])
            specialist = specialist_agents[0]
            return (
                WorkflowBuilder(
                    start_executor=dispatcher,
                    name="LivabilitySpecialistWorkflow",
                    description="選択された1つの住みやすさ専門Agentを実行して構造化結果を集約する",
                    output_from=[aggregator],
                )
                .add_edge(dispatcher, specialist)
                .add_edge(specialist, aggregator)
                .build()
            )

        aggregator = SpecialistResultAggregator(selected)
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
        for axis, evidence in evidence_by_axis.items():
            if evidence.axis != axis:
                raise ValueError("Evidence does not match the selected axis")
        workflow = self.build_specialist_workflow(enabled_axes)
        events = await workflow.run(SpecialistInput(evidence_by_axis=evidence_by_axis))
        outputs = events.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], SpecialistBatch):
            raise RuntimeError("Specialist workflow returned an unexpected output.")
        outcomes = outputs[0].outcomes
        fallbacks = [outcome for outcome in outcomes if outcome.used_fallback]
        if len(enabled_axes) == 1:
            detail = (
                "WorkflowBuilderの単一路で1専門エージェントを実行: " + AXIS_LABELS[enabled_axes[0]]
            )
        else:
            detail = (
                "WorkflowBuilderのfan-out/fan-inで"
                f"{len(enabled_axes)}専門エージェントを並列実行: "
                + "、".join(AXIS_LABELS[axis] for axis in enabled_axes)
            )
        if fallbacks:
            detail += " / 決定論的フォールバック: " + "、".join(
                f"{AXIS_LABELS[item.axis]} ({item.detail})" for item in fallbacks
            )
        return {item.axis: item.narrative for item in outcomes}, bool(fallbacks), detail

    async def research_axes(
        self,
        *,
        region: RegionInfo,
        provider: RegionalDataProvider,
        enabled_axes: Sequence[Axis],
        user_request: str,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[dict[Axis, AxisEvidence], dict[Axis, AxisNarrative], bool, str]:
        """Run each enabled specialist branch from its own data fetch through analysis."""

        selected = [axis for axis in Axis if axis in {Axis(axis) for axis in enabled_axes}]
        if not selected:
            raise ValueError("At least one specialist Agent must be enabled.")

        async def collect(axis: Axis) -> AxisEvidence:
            evidence = await asyncio.wait_for(
                provider.fetch_axis(region, axis),
                timeout=self._settings.data_timeout_seconds,
            )
            if (
                evidence.axis != axis
                or evidence.region.municipality_code != region.municipality_code
            ):
                raise ValueError(f"Evidence identity mismatch for axis={axis.value}")
            if evidence.data_mode not in {"mock", "open_data", "government_api"}:
                raise ValueError(f"Unsupported provider data mode for axis={axis.value}")
            if evidence.data_mode != self._settings.data_mode:
                raise ValueError(f"Evidence data mode mismatch for axis={axis.value}")
            if evidence.data_mode in {"government_api", "open_data"} and any(
                metric.is_mock for metric in evidence.metrics
            ):
                raise ValueError("Live specialist research cannot contain mock metrics")
            if not any(
                metric.quality > 0 and metric.direction != "context_only"
                for metric in evidence.metrics
            ):
                raise ValueError(f"No scorable evidence for axis={axis.value}")

            facts = context_metrics(region, axis)
            if facts:
                evidence = evidence.model_copy(update={"metrics": [*evidence.metrics, *facts]})
            if axis == Axis.FAMILY and not requests_tertiary_education_context(user_request):
                evidence = evidence.model_copy(
                    update={
                        "metrics": [
                            metric
                            for metric in evidence.metrics
                            if metric.metric_code != "tertiary_education_campuses"
                        ]
                    }
                )
            living_conditions = user_request.split("。条件: ", 1)[-1]
            if (
                axis == Axis.FAMILY
                and "独身" in living_conditions
                and not any(term in living_conditions for term in ("子育て", "子ども", "子供"))
            ):
                childcare_codes = {"nursery_per_1000_children", "schools_per_1000_children"}
                evidence = evidence.model_copy(
                    update={
                        "metrics": [
                            metric.model_copy(
                                update={
                                    "direction": "context_only",
                                    "note": ((metric.note + " / ") if metric.note else "")
                                    + (
                                        "独身の条件では採点対象外。将来の子育てを希望する場合は条件を変更。"
                                    ),
                                }
                            )
                            if metric.metric_code in childcare_codes
                            else metric
                            for metric in evidence.metrics
                        ]
                    }
                )
            if progress is not None:
                await progress(
                    f"{AXIS_LABELS[axis]}担当が{len(evidence.api_calls)}件のデータ参照を取得"
                )
            return evidence

        # The workflow engine's fan-out does not cancel sibling branches when a
        # provider fails. Fetch first with explicit cancellation so a failed data
        # read cannot leave other providers running or send partial data to Agents.
        fetch_tasks = {
            axis: asyncio.create_task(collect(axis), name=f"collect-{axis.value}")
            for axis in selected
        }
        try:
            fetched = await asyncio.gather(*fetch_tasks.values())
        except BaseException:
            for task in fetch_tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*fetch_tasks.values(), return_exceptions=True)
            raise
        evidence_by_axis = dict(zip(fetch_tasks, fetched, strict=True))

        # Once all evidence is valid, selected specialists analyze only their own
        # packet in the workflow's fan-out/fan-in graph.
        workflow = self.build_specialist_workflow(selected)
        events = await workflow.run(
            SpecialistInput(
                evidence_by_axis=evidence_by_axis,
                user_request=user_request,
            )
        )
        outputs = events.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], SpecialistBatch):
            raise RuntimeError("Specialist research workflow returned an unexpected output.")
        outcomes = outputs[0].outcomes
        narratives = {item.axis: item.narrative for item in outcomes}
        evidence = {item.axis: item.evidence for item in outcomes if item.evidence is not None}
        if set(narratives) != set(selected) or set(evidence) != set(selected):
            raise RuntimeError("Specialist research workflow returned an incomplete axis set.")
        fallbacks = [item for item in outcomes if item.used_fallback]
        if len(selected) == 1:
            detail = f"1専門エージェントが担当データを収集・分析: {AXIS_LABELS[selected[0]]}"
        else:
            detail = (
                f"WorkflowBuilderのfan-out/fan-inで{len(selected)}専門エージェントが"
                "担当データを個別分析（データ取得は軸ごとに並列）: "
                + "、".join(AXIS_LABELS[axis] for axis in selected)
            )
        if fallbacks:
            detail += " / 決定論的フォールバック: " + "、".join(
                f"{AXIS_LABELS[item.axis]} ({item.detail})" for item in fallbacks
            )
        return evidence, narratives, bool(fallbacks), detail

    async def compare_candidates(
        self,
        *,
        reports: Sequence[AssessmentReport],
        user_request: str,
        weights: dict[Axis, float],
    ) -> CandidateComparison:
        "Give the commander every specialist finding and validate its qualitative recommendation."

        if not 2 <= len(reports) <= 4:
            raise ValueError("Candidate comparison requires two to four reports.")
        codes = [report.plan.region.municipality_code for report in reports]
        if len(codes) != len(set(codes)):
            raise ValueError("Candidate comparison contains duplicate municipalities.")
        axis_maps = [{result.axis: result for result in report.axis_results} for report in reports]
        shared_axes: list[Axis] = []
        for axis in Axis:
            metric_sets = []
            for result_map in axis_maps:
                result = result_map.get(axis)
                metric_sets.append(
                    frozenset(
                        metric.metric_code
                        for metric in result.metrics
                        if metric.quality > 0 and metric.direction != "context_only"
                    )
                    if result
                    else frozenset()
                )
            if metric_sets[0] and all(metrics == metric_sets[0] for metrics in metric_sets):
                shared_axes.append(axis)

        candidate_payloads: list[dict[str, Any]] = []
        for report in reports:
            code = report.plan.region.municipality_code
            candidate: dict[str, Any] = {
                "region_code": code,
                "region_name": report.plan.region.name,
                "data_mode": report.plan.data_mode,
                "unavailable_axes": [axis.value for axis in report.plan.unavailable_axes],
                "unavailable_axis_reasons": {
                    axis.value: reason
                    for axis, reason in report.plan.unavailable_axis_reasons.items()
                },
                "specialist_findings": [],
            }
            for result in report.axis_results:
                candidate["specialist_findings"].append(
                    {
                        "axis": result.axis.value,
                        "label": result.label,
                        "score": result.score,
                        "confidence": result.confidence,
                        "summary": result.narrative.summary,
                        "strengths": result.narrative.strengths,
                        "cautions": result.narrative.cautions,
                        "metrics": [
                            {
                                "metric_code": metric.metric_code,
                                "label": metric.label,
                                "life_meaning": metric_life_meaning(metric.metric_code),
                                "value": metric.value,
                                "unit": metric.unit,
                                "weight": metric.weight,
                                "normalized_score": metric.normalized_score,
                                "direction": metric.direction,
                                "quality": metric.quality,
                                "missing_reason": metric.missing_reason,
                                "note": metric.note,
                                "reference_date": metric.source.reference_date,
                                "source_name": metric.source.source_name,
                                "source_id": metric.source.source_id,
                                "sample_count": metric.sample_count,
                                "is_mock": metric.is_mock,
                            }
                            for metric in result.metrics
                        ],
                    }
                )
            candidate_payloads.append(candidate)

        total_requested_weight = sum(max(0.0, weights.get(axis, 0.0)) for axis in Axis)
        priority_weights = (
            {
                axis.value: round(
                    max(0.0, weights.get(axis, 0.0)) / total_requested_weight * 100, 1
                )
                for axis in Axis
            }
            if total_requested_weight > 0
            else {}
        )
        commander_payload = {
            "user_request": user_request,
            "data_mode": reports[0].plan.data_mode,
            "priority_weights": priority_weights,
            "comparable_axes": [axis.value for axis in shared_axes],
            "comparable_metric_codes": sorted(comparable_metric_codes(reports)),
            "candidates": candidate_payloads,
        }
        if not shared_axes:
            return CandidateComparison(
                recommended_region_code=None,
                shared_axes=[],
                narrative=build_commander_narrative(commander_payload),
                used_fallback=True,
            )
        prompt = (
            "5軸の専門エージェントが集めた候補地ごとの調査結果を読み、"
            "利用者の生活条件に合わせた最初の提案、具体的な理由、譲る点、次の確認事項をまとめてください。\n"
            f"{PAYLOAD_MARKER}\n{json.dumps(commander_payload, ensure_ascii=False)}"
        )
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    self.commander.run(
                        prompt,
                        session=self.commander.create_session(),
                        options={"response_format": CommanderNarrative},
                    ),
                    timeout=self._settings.agent_timeout_seconds,
                )
                value = response.value
                narrative = (
                    value
                    if isinstance(value, CommanderNarrative)
                    else CommanderNarrative.model_validate_json(response.text)
                )
                if narrative.recommended_region_code not in {*codes, None}:
                    raise ValueError("Commander selected an unknown municipality")
                narrative = validate_sales_proposal(narrative, reports)
            except (ValidationError, ValueError):
                if attempt == 0:
                    prompt = (
                        "前回の回答は形式検証に失敗しました。候補・根拠・利用者条件を変えず、"
                        "すべて自然な日本語で、不可視の制御記号や編集用マーカーを含めず、"
                        "指定されたスキーマに合う回答を作り直してください。\n" + prompt
                    )
                    continue
                logger.warning(
                    "Commander comparison output failed validation; using safe fallback."
                )
                break
            except Exception as exc:
                logger.warning(
                    "Commander comparison failed; %s. Using evidence-based fallback.",
                    _safe_commander_error_context(exc),
                )
                break
            else:
                return CandidateComparison(
                    recommended_region_code=narrative.recommended_region_code,
                    shared_axes=shared_axes,
                    narrative=narrative,
                    used_fallback=self._settings.resolved_llm_mode != "openai",
                )

        narrative = build_commander_narrative(commander_payload)
        return CandidateComparison(
            recommended_region_code=narrative.recommended_region_code,
            shared_axes=shared_axes,
            narrative=narrative,
            used_fallback=True,
        )

    async def create_knowledge_only_assessment(
        self,
        *,
        user_request: str,
        region_name: str,
        enabled_axes: Sequence[Axis],
    ) -> tuple[KnowledgeOnlyAssessment, bool, str]:
        """Ask the configured LLM for a no-external-data, explicitly approximate view."""

        selected = [Axis(axis) for axis in enabled_axes]
        payload = {
            "user_request": user_request,
            "region_name": region_name,
            "enabled_axes": [axis.value for axis in selected],
        }
        prompt = (
            "外部データAPIを呼ばず、LLM知識だけでこの予備評価を作成してください。\n"
            f"{PAYLOAD_MARKER}\n{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            response = await asyncio.wait_for(
                self.knowledge_evaluator.run(
                    prompt,
                    session=self.knowledge_evaluator.create_session(),
                    options={"response_format": KnowledgeOnlyAssessment},
                ),
                timeout=self._settings.agent_timeout_seconds,
            )
            value = response.value
            assessment = (
                value
                if isinstance(value, KnowledgeOnlyAssessment)
                else KnowledgeOnlyAssessment.model_validate_json(response.text)
            )
            if assessment.region_name != region_name:
                raise ValueError("Knowledge-only response region does not match the request")
            by_axis = {item.axis: item for item in assessment.axis_assessments}
            if len(by_axis) != len(selected) or set(by_axis) != set(selected):
                raise ValueError("Knowledge-only response axes do not match the request")
            if any(item.narrative.axis != item.axis for item in by_axis.values()):
                raise ValueError("Knowledge-only narrative axis does not match the request")
            assessment = assessment.model_copy(
                update={"axis_assessments": [by_axis[axis] for axis in selected]}
            )
            return assessment, False, "LLM知識のみ（外部データAPIは未使用）"
        except Exception as exc:
            return (
                build_knowledge_only_assessment(region_name, user_request, selected),
                True,
                "オフライン代替を使用: " + f"{type(exc).__name__}: {exc}",
            )

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
