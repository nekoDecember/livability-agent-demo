from __future__ import annotations

import hashlib
from collections.abc import Sequence

from .models import (
    AXIS_LABELS,
    Axis,
    AxisEvidence,
    AxisNarrative,
    AxisResult,
    FinalNarrative,
    KnowledgeOnlyAssessment,
    KnowledgeOnlyAxisAssessment,
)

KNOWLEDGE_ONLY_SCORE_SETS: dict[str, dict[Axis, float]] = {
    "流山市": {
        Axis.CONVENIENCE: 72,
        Axis.HOUSING: 62,
        Axis.FAMILY: 82,
        Axis.SAFETY: 58,
        Axis.FUTURE: 80,
    },
    "柏市": {
        Axis.CONVENIENCE: 84,
        Axis.HOUSING: 66,
        Axis.FAMILY: 76,
        Axis.SAFETY: 59,
        Axis.FUTURE: 69,
    },
    "武蔵野市": {
        Axis.CONVENIENCE: 92,
        Axis.HOUSING: 35,
        Axis.FAMILY: 82,
        Axis.SAFETY: 68,
        Axis.FUTURE: 75,
    },
    "横浜市": {
        Axis.CONVENIENCE: 83,
        Axis.HOUSING: 55,
        Axis.FAMILY: 78,
        Axis.SAFETY: 57,
        Axis.FUTURE: 76,
    },
    "さいたま市": {
        Axis.CONVENIENCE: 80,
        Axis.HOUSING: 68,
        Axis.FAMILY: 75,
        Axis.SAFETY: 61,
        Axis.FUTURE: 78,
    },
    "千代田区": {
        Axis.CONVENIENCE: 98,
        Axis.HOUSING: 18,
        Axis.FAMILY: 72,
        Axis.SAFETY: 64,
        Axis.FUTURE: 84,
    },
}


def _knowledge_only_score(region_name: str, axis: Axis) -> float:
    known = KNOWLEDGE_ONLY_SCORE_SETS.get(region_name)
    if known and axis in known:
        return known[axis]
    digest = hashlib.sha256(f"{region_name}|{axis.value}".encode()).hexdigest()
    return float(42 + int(digest[:4], 16) % 38)


def build_knowledge_only_assessment(
    region_name: str,
    user_request: str,
    enabled_axes: Sequence[Axis],
) -> KnowledgeOnlyAssessment:
    """Build a transparent offline substitute for the knowledge-only LLM path."""

    del user_request
    assessments: list[KnowledgeOnlyAxisAssessment] = []
    for axis in enabled_axes:
        score = _knowledge_only_score(region_name, axis)
        label = AXIS_LABELS[axis]
        assessments.append(
            KnowledgeOnlyAxisAssessment(
                axis=axis,
                score=score,
                confidence=0.20,
                narrative=AxisNarrative(
                    axis=axis,
                    summary=(
                        f"{label}は、外部データを参照しない一般知識ベースの"
                        f"仮説として{score:.0f}点相当です。"
                    ),
                    strengths=[f"{label}について一般的な地域イメージでは判断材料があります"],
                    cautions=[
                        "最新の数値・駅や町丁目ごとの差・個別物件条件は確認できません",
                        "この点数は測定値ではなく、LLM知識のみの粗い見立てです",
                    ],
                ),
            )
        )

    high = sorted(assessments, key=lambda item: item.score, reverse=True)
    low = list(reversed(high))
    strengths = [
        f"{AXIS_LABELS[item.axis]}が{item.score:.0f}点で、一般知識ベースの強みです。"
        for item in high[:2]
    ]
    cautions = [
        f"{AXIS_LABELS[item.axis]}は{item.score:.0f}点で、現地条件の確認が必要です。"
        for item in low[:2]
    ]
    cautions.append("外部データAPIを使っていないため、最新性と数値の正確性は保証しません。")
    return KnowledgeOnlyAssessment(
        region_name=region_name,
        axis_assessments=assessments,
        narrative=FinalNarrative(
            executive_summary=(
                f"{region_name}を、外部データAPIなし・LLMの一般知識だけで見た"
                "予備評価です。比較の方向性を決めるための仮説として扱ってください。"
            ),
            strengths=strengths[:3],
            cautions=cautions[:3],
            suggested_followups=[
                "候補を並べて第一候補を決め、重要な弱点だけ現地確認する",
                "同じ候補を外部データありモードでも再評価する",
            ],
        ),
    )


def build_axis_narrative(evidence: AxisEvidence) -> AxisNarrative:
    ranked = sorted(evidence.metrics, key=lambda metric: metric.normalized_score, reverse=True)
    strengths = [
        f"{metric.label}は比較対象内で相対的に良好（指標スコア {metric.normalized_score:.0f}）"
        for metric in ranked[:2]
    ]
    cautions = [
        f"{metric.label}は確認が必要（指標スコア {metric.normalized_score:.0f}）"
        for metric in sorted(evidence.metrics, key=lambda metric: metric.normalized_score)[:2]
    ]
    if any(metric.is_mock for metric in evidence.metrics):
        cautions[-1] = "現在はモック値。API接続後に同じ計算契約で実データへ差し替える"

    average = sum(metric.normalized_score * metric.weight for metric in evidence.metrics) / sum(
        metric.weight for metric in evidence.metrics
    )
    return AxisNarrative(
        axis=evidence.axis,
        summary=f"{AXIS_LABELS[evidence.axis]}は比較基準に対して概ね{average:.0f}点相当です。",
        strengths=strengths,
        cautions=cautions,
    )


def build_final_narrative(
    region_name: str,
    overall_score: float,
    results: list[AxisResult],
) -> FinalNarrative:
    if not results:
        raise ValueError("At least one axis result is required.")
    high = sorted(results, key=lambda result: result.score, reverse=True)
    low = list(reversed(high))
    strengths = [
        f"{result.label}が{result.score:.1f}点で、相対的な強みです。" for result in high[:3]
    ]
    cautions = [
        f"{result.label}は{result.score:.1f}点で、条件や地域差を追加確認してください。"
        for result in low[:2]
    ]
    if any(metric.is_mock for result in results for metric in result.metrics):
        cautions.append("表示値はデモ用モックデータであり、実在地域の評価には使用できません。")

    excluded_axes = [axis for axis in Axis if axis not in {item.axis for item in results}]
    selection_note = ""
    if excluded_axes:
        selection_note = (
            "除外した軸はAPI取得・専門エージェント分析・総合点に含めていません。"
        )
    followups = [
        "子育て・交通・住居費などの重みを変えて再評価する",
        "比較したい別の市区町村を同じ基準で評価する",
        "API接続後に出典年と欠損指標を確認する",
    ]
    if excluded_axes:
        followups[0] = "除外した専門エージェントを戻し、総合点と根拠数を比較する"
    else:
        followups[0] = "専門エージェントを1つ除外し、総合点と根拠数を比較する"

    return FinalNarrative(
        executive_summary=(
            f"{region_name}の総合評価は{overall_score:.1f}点です。"
            f"{len(results)}つの専門エージェントが同一基準の証拠を"
            f"並列処理した結果です。{selection_note}"
        ),
        strengths=strengths[:3],
        cautions=cautions[:3],
        suggested_followups=followups,
    )
