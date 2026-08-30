from __future__ import annotations

from .models import (
    AXIS_LABELS,
    Axis,
    AxisEvidence,
    AxisNarrative,
    AxisResult,
    FinalNarrative,
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
