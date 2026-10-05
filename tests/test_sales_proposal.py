import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from livability_demo.agent_team import AgentTeam
from livability_demo.config import Settings
from livability_demo.deterministic_analysis import build_commander_narrative
from livability_demo.models import (
    AssessmentPlan,
    AssessmentReport,
    Axis,
    AxisNarrative,
    AxisResult,
    CandidatePosition,
    CommanderNarrative,
    FinalNarrative,
    MetricEvidence,
    RegionInfo,
    SourceReference,
)
from livability_demo.offline_client import PAYLOAD_MARKER
from livability_demo.sales_proposal import validate_sales_proposal


def report(code: str) -> AssessmentReport:
    region = RegionInfo(query=code, name=code, municipality_code=code)
    return AssessmentReport(
        report_id=code,
        generated_at=datetime.now(UTC),
        plan=AssessmentPlan(
            user_request="土地購入を重視",
            region=region,
            weights={Axis.HOUSING: 100},
            enabled_axes=[Axis.HOUSING],
            excluded_axes=[axis for axis in Axis if axis != Axis.HOUSING],
            agent_selection_reason="条件",
            preferences=[],
            weight_reason="条件",
            data_mode="open_data",
        ),
        research_confidence=1,
        axis_results=[
            AxisResult(
                axis=Axis.HOUSING,
                label="土地価格",
                score=70,
                confidence=1,
                elapsed_ms=0,
                narrative=AxisNarrative(
                    axis=Axis.HOUSING, summary="比較", strengths=["比較"], cautions=["確認"]
                ),
                api_calls=[],
                metrics=[
                    MetricEvidence(
                        metric_code="land",
                        label="土地価格",
                        value=50000,
                        unit="円/㎡",
                        normalized_score=70,
                        direction="lower_is_better",
                        weight=1,
                        source=SourceReference(
                            source_id="land",
                            source_name="公式",
                            endpoint="snapshot",
                            url="https://example.go.jp",
                            reference_date="2026",
                            commercial_use_note="可",
                        ),
                    )
                ],
            )
        ],
        narrative=FinalNarrative(
            executive_summary="比較",
            strengths=["比較"],
            cautions=["確認"],
            suggested_followups=["確認"],
        ),
        execution_steps=[],
        total_elapsed_ms=0,
        disclaimers=[],
    )


def narrative(**overrides) -> CommanderNarrative:
    return CommanderNarrative(
        **dict(
            dict(
                recommended_region_code="1",
                summary="第一候補",
                reasons=["土地価格"],
                tradeoffs=["他の条件"],
                next_checks=["物件を見る"],
                confidence=0.8,
                recommendation_strength="recommended",
                supporting_metric_codes=["land"],
            ),
            **overrides,
        ),
    )


def test_accepts_comparable_evidence_and_preserves_clear_recommendation():
    result = validate_sales_proposal(narrative(), [report("1"), report("2")])
    assert result.recommendation_strength == "recommended"
    assert result.supporting_metric_codes == ["land"]


def test_customer_prose_omits_internal_codes_but_keeps_years_and_values():
    result = validate_sales_proposal(
        narrative(
            summary="土地予算を重視するなら第一候補です（2026年、land）。",
            reasons=["土地価格は50000円/㎡です（2026年、land）。", "比較しました（land、land）。"],
            tradeoffs=["背景も比較します (landscape)。"],
            next_checks=["売地を確認しましょう (2026, land)。"],
        ),
        [report("1"), report("2")],
    )
    assert result.summary.endswith("（2026年）。")
    assert result.reasons == ["土地価格は50000円/㎡です（2026年）。", "比較しました。"]
    assert result.tradeoffs == ["背景も比較します (landscape)。"]
    assert result.next_checks == ["売地を確認しましょう (2026)。"]
    assert result.supporting_metric_codes == ["land"]


@pytest.mark.parametrize(
    "reason", ["missing", "year", "unit", "direction", "source", "context", "mock"]
)
def test_rejects_unavailable_or_incomparable_decisive_evidence(reason):
    reports = [report("1"), report("2")]
    metric = reports[1].axis_results[0].metrics[0]
    if reason == "missing":
        metric.quality = 0
    if reason == "year":
        metric.source.reference_date = "2025"
    if reason == "unit":
        metric.unit = "円/坪"
    if reason == "direction":
        metric.direction = "higher_is_better"
    if reason == "source":
        metric.source.source_id = "different"
    if reason == "context":
        metric.direction = "context_only"
    if reason == "mock":
        metric.is_mock = True
    with pytest.raises(ValueError):
        validate_sales_proposal(narrative(), reports)


def test_strong_recommendation_requires_referenced_evidence():
    with pytest.raises(ValueError, match="requires comparable evidence"):
        validate_sales_proposal(narrative(supporting_metric_codes=[]), [report("1"), report("2")])


def test_knowledge_only_result_stays_a_hypothesis():
    reports = [report("1"), report("2")]
    for item in reports:
        item.plan.data_mode = "knowledge_only"
    assert validate_sales_proposal(narrative(), reports).recommendation_strength == "hypothesis"


def test_no_recommended_city_is_undecided():
    result = validate_sales_proposal(
        narrative(recommended_region_code=None), [report("1"), report("2")]
    )
    assert result.recommendation_strength == "undecided"


def test_candidate_positions_must_cover_every_city_once():
    with pytest.raises(ValueError, match="every candidate"):
        validate_sales_proposal(
            narrative(
                candidate_positions=[
                    CandidatePosition(
                        region_code="1", fit_summary="適合", selection_condition="条件"
                    ),
                ]
            ),
            [report("1"), report("2")],
        )


def test_knowledge_fallback_never_claims_to_have_acquired_external_data():
    result = build_commander_narrative(
        {
            "data_mode": "knowledge_only",
            "comparable_axes": ["housing"],
            "priority_weights": {"housing": 100},
            "candidates": [
                {
                    "region_code": str(index),
                    "region_name": f"候補{index}",
                    "specialist_findings": [{"axis": "housing", "score": score}],
                }
                for index, score in [(1, 70), (2, 60)]
            ],
        }
    )
    assert result.recommendation_strength == "hypothesis"
    assert "一般知識からの暫定提案" in result.summary
    assert "取得済みデータ" not in result.summary


@pytest.mark.asyncio
async def test_commander_retries_invalid_evidence_and_delivers_a_grounded_sales_proposal():
    team = AgentTeam(Settings(_env_file=None, llm_mode="mock", data_mode="mock", mock_latency_ms=0))

    class Commander:
        calls = 0

        async def run(self, prompt, *, session, options):
            self.calls += 1
            payload = json.loads(prompt.split(PAYLOAD_MARKER, 1)[1])
            assert payload["data_mode"] == "open_data"
            assert payload["comparable_metric_codes"] == ["land"]
            assert (
                payload["candidates"][0]["specialist_findings"][0]["metrics"][0]["source_id"]
                == "land"
            )
            return SimpleNamespace(
                value=narrative(
                    supporting_metric_codes=["invented"] if self.calls == 1 else ["land"],
                    proposal_title="土地購入を重視するなら候補1",
                    candidate_positions=[
                        CandidatePosition(
                            region_code=code,
                            fit_summary="価格を比較",
                            selection_condition="土地購入",
                        )
                        for code in ["1", "2"]
                    ],
                )
            )

        def create_session(self):
            return None

    commander = Commander()
    team.commander = commander
    result = await team.compare_candidates(
        reports=[report("1"), report("2")], user_request="土地購入", weights={Axis.HOUSING: 100}
    )
    assert commander.calls == 2
    assert result.narrative.recommendation_strength == "recommended"
    assert result.narrative.supporting_metric_codes == ["land"]
    assert len(result.narrative.candidate_positions) == 2
