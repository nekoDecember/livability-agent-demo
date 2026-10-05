from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from math import isfinite
from typing import Any

from .models import (
    AXIS_LABELS,
    Axis,
    AxisEvidence,
    AxisNarrative,
    AxisResult,
    CandidatePosition,
    CommanderNarrative,
    FinalNarrative,
    KnowledgeOnlyAssessment,
    KnowledgeOnlyAxisAssessment,
)
from .regional_context import requests_tertiary_education_context

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
    available = [
        metric
        for metric in evidence.metrics
        if metric.quality > 0 and metric.direction != "context_only"
    ]
    if not available:
        raise ValueError(f"No available evidence for axis={evidence.axis.value}")
    ranked = sorted(available, key=lambda metric: metric.normalized_score, reverse=True)
    strengths = [
        f"{metric_life_meaning(metric.metric_code)}"
        f"（{metric.value:g}{metric.unit}、{metric.source.reference_date}）"
        for metric in ranked[:2]
    ]
    cautions = [
        f"{metric_life_meaning(metric.metric_code)}"
        f"（{metric.value:g}{metric.unit}、{metric.source.reference_date}）"
        for metric in sorted(available, key=lambda metric: metric.normalized_score)[:2]
    ]
    missing_count = sum(metric.quality == 0 for metric in evidence.metrics)
    if missing_count:
        cautions.append(f"{missing_count}指標はデータがなく、今回の比較に含めていません")
    if any(metric.is_mock for metric in evidence.metrics):
        cautions[-1] = "今回はデモ用の仮データです"

    summary = build_axis_summary(evidence)
    tertiary_metric = next(
        (
            metric
            for metric in evidence.metrics
            if metric.metric_code == "tertiary_education_campuses"
            and metric.quality > 0
            and metric.direction == "context_only"
        ),
        None,
    )
    if tertiary_metric:
        summary += (
            f" 高等教育の参考として、市内には大学・短大・高専の掲載キャンパスが"
            f"{tertiary_metric.value:g}{tertiary_metric.unit}あります。"
        )

    return AxisNarrative(
        axis=evidence.axis,
        summary=summary,
        strengths=strengths,
        cautions=cautions[:3],
    )


METRIC_LIFE_MEANINGS: dict[str, str] = {
    "station_density": "鉄道駅を利用できるエリアの広がりに関わります",
    "railway_lines": "鉄道で向かえる方面や経路の選択肢に関わります",
    "station_passengers": "市内の主な駅の規模や交通拠点の大きさを表します",
    "takasaki_shinkansen_connections": (
        "高崎駅から上越・北陸新幹線を利用でき、遠方への移動に使えます"
    ),
    "retail_density": "日々の買い物や用事に使える店・サービスの多さに関わります",
    "daytime_population_ratio": "昼間に通勤や用事で人が集まる街かどうかの目安です",
    "transaction_unit_price": "住宅を購入するときの予算感に関わります",
    "official_land_price": "土地を買って家を建てる場合の土地代に関わります",
    "residential_land_price": "土地を買って家を建てる場合の土地代に関わります",
    "housing_stock": "市全体の住まいの供給にどれだけ厚みがあるかを表します",
    "clinics_per_100k": "日常的に利用する診療所の多さで、受診先の選択肢に関わります",
    "emergency_hospitals": "急な体調不良に対応する医療施設の数を表します",
    "nursery_per_1000_children": "子どもの人口に対する保育・幼児教育施設の多さを表します",
    "schools_per_1000_children": "子どもの人口に対する学校数で、市全体の学校の厚みを表します",
    "tertiary_education_campuses": "大学・短大・高専のキャンパス数で、進学環境の背景情報です",
    "crime_rate": "人口に対する刑法犯の認知件数で、市全体の発生傾向を表します",
    "traffic_accidents": "人口に対する交通事故件数で、市全体の事故の起こりやすさを表します",
    "flood_exposure": "市内で洪水浸水想定区域がどの程度広がるかの目安です",
    "landslide_exposure": "市内で土砂災害警戒区域がどの程度広がるかの目安です",
    "shelters_per_10k": "人口あたりの指定緊急避難場所の数で、避難先の数の目安です",
    "population_retention_2040": "2040年に現在の人口規模がどの程度保たれるかの見通しです",
    "population_retention_2050": "2050年に現在の人口規模がどの程度保たれるかの見通しです",
    "working_age_retention": "将来の働く世代の人口規模がどの程度保たれるかの見通しです",
    "child_population_retention": "将来の子どもの人口規模がどの程度保たれるかの見通しです",
}


def metric_life_meaning(metric_code: str) -> str:
    """Translate a public statistic into its everyday-life interpretation."""

    return METRIC_LIFE_MEANINGS.get(metric_code, "市全体の暮らしの傾向を表す指標です")


COMMANDER_FALLBACK_AXIS_MARGIN = 2.0

_COMMANDER_METRIC_PHRASES: dict[str, tuple[str, str, str]] = {
    "station_density": ("鉄道駅を使えるエリア", "広い", "狭い"),
    "railway_lines": ("鉄道で行ける方面や路線", "多い", "少ない"),
    "station_passengers": ("主な駅の利用規模", "大きい", "小さい"),
    "retail_density": ("買い物や用事に使える店・サービス", "多い", "少ない"),
    "daytime_population_ratio": ("昼間に人が集まる度合い", "高い", "低い"),
    "transaction_unit_price": ("住宅を購入するときの価格の目安", "高い", "低い"),
    "official_land_price": ("土地を買って家を建てる場合の土地代", "高い", "低い"),
    "residential_land_price": ("土地を買って家を建てる場合の土地代", "高い", "低い"),
    "housing_stock": ("住まいの供給", "厚い", "薄い"),
    "clinics_per_100k": ("日常的な受診先の選択肢", "多い", "少ない"),
    "emergency_hospitals": ("救急対応の医療施設", "多い", "少ない"),
    "nursery_per_1000_children": ("子どもの人数に対する保育・幼児教育の受け皿", "多い", "少ない"),
    "schools_per_1000_children": ("子どもの人数に対する学校", "多い", "少ない"),
    "crime_rate": ("人口あたりの刑法犯認知件数", "高い", "低い"),
    "traffic_accidents": ("人口あたりの交通事故件数", "多い", "少ない"),
    "flood_exposure": ("洪水浸水想定区域の広がり", "大きい", "小さい"),
    "landslide_exposure": ("土砂災害警戒区域の広がり", "大きい", "小さい"),
    "shelters_per_10k": ("人口あたりの指定緊急避難場所", "多い", "少ない"),
    "population_retention_2040": ("2040年の人口規模を保つ見通し", "高い", "低い"),
    "population_retention_2050": ("2050年の人口規模を保つ見通し", "高い", "低い"),
    "working_age_retention": ("働く世代の人口を保つ見通し", "高い", "低い"),
    "child_population_retention": ("子どもの人口を保つ見通し", "高い", "低い"),
}


def _commander_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if isfinite(score) else None


def _commander_findings(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(finding.get("axis")): finding
        for finding in candidate.get("specialist_findings", [])
        if isinstance(finding, dict) and finding.get("axis")
    }


def _commander_metric_reason(
    axis: str,
    candidates: list[dict[str, Any]],
    findings: list[dict[str, dict[str, Any]]],
    winner_index: int,
) -> str | None:
    """Describe the strongest measured fact that supports the selected candidate."""

    metric_rows: dict[str, list[dict[str, Any] | None]] = {}
    for candidate_index, axis_map in enumerate(findings):
        finding = axis_map.get(axis, {})
        for metric in finding.get("metrics", []):
            if (
                isinstance(metric, dict)
                and metric.get("quality", 1) > 0
                and metric.get("direction") != "context_only"
            ):
                metric_rows.setdefault(
                    str(metric.get("metric_code", "")), [None] * len(candidates)
                )[candidate_index] = metric

    best: tuple[float, dict[str, Any], int] | None = None
    for rows in metric_rows.values():
        if any(row is None for row in rows):
            continue
        winner_metric = rows[winner_index]
        assert winner_metric is not None
        winner_score = _commander_score(winner_metric.get("normalized_score"))
        if winner_score is None:
            continue
        other_scores: list[tuple[int, float]] = []
        for index, row in enumerate(rows):
            if index == winner_index:
                continue
            assert row is not None
            score = _commander_score(row.get("normalized_score"))
            if score is not None:
                other_scores.append((index, score))
        if not other_scores:
            continue
        closest_competitor, closest_score = max(other_scores, key=lambda item: item[1])
        gap = winner_score - closest_score
        if gap <= 0:
            continue
        metric_weight = _commander_score(winner_metric.get("weight")) or 1.0
        support = gap * metric_weight
        if best is None or support > best[0]:
            best = (support, winner_metric, closest_competitor)

    if best is None:
        return None
    _, winner_metric, competitor_index = best
    code = str(winner_metric.get("metric_code", ""))
    if code not in _COMMANDER_METRIC_PHRASES:
        return None
    phrase, higher_word, lower_word = _COMMANDER_METRIC_PHRASES[code]
    direction = str(winner_metric.get("direction", ""))
    favorable_word = higher_word if direction == "higher_is_better" else lower_word
    winner_value = _commander_score(winner_metric.get("value"))
    if winner_value is None:
        return None

    competitor_metric = next(
        (
            metric
            for metric in findings[competitor_index].get(axis, {}).get("metrics", [])
            if isinstance(metric, dict) and metric.get("metric_code") == code
        ),
        None,
    )
    competitor_value = (
        _commander_score(competitor_metric.get("value")) if competitor_metric else None
    )
    winner_unit = str(winner_metric.get("unit", ""))
    competitor_unit = str((competitor_metric or {}).get("unit", winner_unit))
    unit = winner_unit
    if competitor_unit and competitor_unit != winner_unit:
        unit = f"（{winner_unit}/{competitor_unit}）"
    reference_date = str(winner_metric.get("reference_date", ""))
    reference = f"（{reference_date}）" if reference_date else ""
    winner_name = str(candidates[winner_index].get("region_name", "第一候補"))
    competitor_name = str(candidates[competitor_index].get("region_name", "他候補"))
    value_comparison = (
        f"{winner_name}が{winner_value:g}{unit}、"
        f"{competitor_name}が{competitor_value:g}{competitor_unit}で、"
        f"{winner_name}のほうが{favorable_word}"
        if competitor_value is not None
        else f"第一候補の値は{winner_value:g}{unit}です。"
    )
    return f"{metric_life_meaning(code)}。{phrase}は、{value_comparison}{reference}。"


def build_axis_summary(evidence: AxisEvidence, user_request: str = "") -> str:
    living_conditions = user_request.split("。条件: ", 1)[-1]
    available = [
        metric
        for metric in evidence.metrics
        if metric.quality > 0 and metric.direction != "context_only"
    ]
    if not available:
        raise ValueError(f"No available evidence for axis={evidence.axis.value}")
    car_commute = any(
        term in living_conditions for term in ("車通勤", "車で通勤", "車で通う", "自動車通勤")
    )
    rail_commute = any(
        term in living_conditions for term in ("電車通勤", "鉄道通勤", "新幹線通勤", "新幹線で通勤")
    )
    if evidence.axis == Axis.CONVENIENCE and not car_commute and rail_commute:
        preferred = ("railway_lines", "station_density", "retail_density")
    elif (
        evidence.axis == Axis.CONVENIENCE
        and not car_commute
        and ("車なし" in living_conditions or "公共交通" in living_conditions)
    ):
        preferred = ("station_density", "railway_lines", "retail_density")
    elif evidence.axis == Axis.FAMILY and "子育て" in living_conditions:
        preferred = ("nursery_per_1000_children", "schools_per_1000_children", "clinics_per_100k")
    else:
        preferred = ()
    chosen = next(
        (metric for code in preferred for metric in available if metric.metric_code == code), None
    )
    chosen = chosen or max(available, key=lambda metric: metric.weight * metric.quality)
    # A station fact is useful as context for rail users, but should not be framed as
    # commute suitability when the person has explicitly said they drive.
    fact = next(
        (
            metric
            for metric in evidence.metrics
            if metric.metric_code == "takasaki_shinkansen_connections"
        ),
        None,
    )
    if car_commute:
        fact = None
    fact_text = "高崎駅には上越・北陸新幹線があり、遠方への移動にも使えます。" if fact else ""
    tertiary_metric = next(
        (
            metric
            for metric in evidence.metrics
            if metric.metric_code == "tertiary_education_campuses"
            and metric.quality > 0
            and metric.direction == "context_only"
        ),
        None,
    )
    tertiary_text = (
        f"市内には大学・短大・高専の掲載キャンパスが"
        f"{tertiary_metric.value:g}{tertiary_metric.unit}あり、進学環境の参考になります。"
        if (
            evidence.axis == Axis.FAMILY
            and requests_tertiary_education_context(user_request)
            and tertiary_metric
        )
        else ""
    )
    if chosen.is_mock:
        return (
            f"{evidence.region.name}の{AXIS_LABELS[evidence.axis]}はデモ値です。"
            "実在地域の判断には使えません。"
        )
    years = list(dict.fromkeys(re.findall(r"20\d{2}年", chosen.note or "")))
    reference = "・".join(years) if years else chosen.source.reference_date
    value_text = f"{chosen.value:g}{chosen.unit}（{reference}）"
    life_meaning = metric_life_meaning(chosen.metric_code)
    if chosen.metric_code in {"official_land_price", "residential_land_price"}:
        life_meaning += "（建物の建築費や家賃ではなく、土地分の目安です）"
    return (
        f"{evidence.region.name}では、{fact_text}{life_meaning}。"
        f"根拠となる数値は{value_text}です。{tertiary_text}"
    )


def build_candidate_narrative(
    region_name: str,
    results: list[AxisResult],
    user_request: str = "",
    weights: dict[Axis, float] | None = None,
) -> FinalNarrative:
    if not results:
        raise ValueError("At least one axis result is required.")
    priorities = sorted(
        results,
        key=lambda result: (weights or {}).get(result.axis, 0),
        reverse=True,
    )
    strengths = [result.narrative.summary for result in priorities[:3]]
    cautions = [result.narrative.cautions[0] for result in priorities if result.narrative.cautions][
        :3
    ]
    if any(metric.is_mock for result in results for metric in result.metrics):
        cautions.append("表示値はデモ用モックデータであり、実在地域の評価には使用できません。")

    condition = user_request.split("。条件:", 1)[-1].strip() if "。条件:" in user_request else ""
    followups: list[str] = []
    if any(term in condition for term in ("子育て", "子ども", "子供", "保育")):
        followups.append("候補の住居から保育施設までの距離と、最新の空き状況を確認する")
    if any(term in condition for term in ("家賃", "住宅費", "住居費", "予算")):
        followups.append("希望する間取り・築年の募集中物件を同じ条件で確認する")
    if any(term in condition for term in ("通勤", "勤務地")) and "通勤なし" not in condition:
        followups.append("実際の住居候補から勤務地までの経路と所要時間を確認する")
    if any(term in condition for term in ("防災", "災害", "洪水", "浸水")):
        followups.append("候補物件の住所を自治体のハザードマップで確認する")
    if not followups:
        followups.append("候補となる別の自治体を同じ生活条件で調査する")
    followups.append("出典の基準年と、未取得指標が条件に影響しないか確認する")

    priority_labels = "・".join(result.label for result in priorities[:3])
    condition_note = f"今回の生活条件「{condition}」を受け、" if condition else ""
    return FinalNarrative(
        executive_summary=(
            f"{region_name}の候補別調査記録です。{condition_note}"
            f"{priority_labels}の専門Agentが取得根拠を整理しました。"
            "候補間の提案は、司令塔が各候補の所見と利用者条件を比較して行います。"
        ),
        strengths=strengths[:3],
        cautions=cautions[:3],
        suggested_followups=followups[:3],
    )


def build_commander_narrative(payload: dict[str, Any]) -> CommanderNarrative:
    """Produce a profile-aware, evidence-grounded recommendation if the commander fails."""

    candidates = payload.get("candidates", [])
    comparable_axes = payload.get("comparable_axes", payload.get("shared_axes", []))
    preference = str(payload.get("user_request", ""))
    names = [str(candidate.get("region_name", "候補地")) for candidate in candidates]
    findings = [_commander_findings(candidate) for candidate in candidates]
    axis_labels: dict[str, str] = {}
    for candidate_findings in findings:
        for axis, finding in candidate_findings.items():
            axis_labels.setdefault(axis, str(finding.get("label", axis)))
    priority_weights = payload.get("priority_weights", {})
    parsed_weights = {
        str(axis): weight
        for axis, raw_weight in priority_weights.items()
        if (weight := _commander_score(raw_weight)) is not None and weight > 0
    }

    scored_axes: list[str] = []
    axis_scores: dict[str, list[float]] = {}
    if len(candidates) >= 2:
        for axis in comparable_axes:
            axis = str(axis)
            scores: list[float] = []
            for candidate_findings in findings:
                score = _commander_score(candidate_findings.get(axis, {}).get("score"))
                if score is None:
                    break
                scores.append(score)
            if len(scores) == len(candidates):
                scored_axes.append(axis)
                axis_scores[axis] = scores
    if len(candidates) < 2 or not scored_axes:
        return CommanderNarrative(
            recommended_region_code=None,
            summary="候補間で同じ指標の比較結果が揃わず、まだ第一候補を決める根拠がありません。",
            recommendation_strength="undecided",
            reasons=["同じ条件で比べられる取得済みデータがありません。"],
            tradeoffs=["まずは両候補で共通して取得できる指標を増やしてください。"],
            next_checks=["候補地ごとの未取得データと公開元を確認する"],
            confidence=0.0,
        )

    comparable_order = {str(axis): index for index, axis in enumerate(comparable_axes)}
    ordered_axes = sorted(
        scored_axes,
        key=lambda axis: (
            -parsed_weights.get(axis, 0.0),
            comparable_order.get(axis, len(comparable_order)),
        ),
    )
    winner_index = 0
    decision_axis = ordered_axes[0]
    winning_gap = 0.0
    decisive = False
    for axis in ordered_axes:
        ranked = sorted(enumerate(axis_scores[axis]), key=lambda item: item[1], reverse=True)
        gap = ranked[0][1] - ranked[1][1]
        if gap >= COMMANDER_FALLBACK_AXIS_MARGIN:
            winner_index = ranked[0][0]
            decision_axis = axis
            winning_gap = gap
            decisive = True
            break

    if not decisive:
        # Keep the configured priority order: each axis breaks a near-tie on the
        # preceding one. Input order is the final stable tie-break for equal data.
        winner_index = max(
            range(len(candidates)),
            key=lambda index: tuple(axis_scores[axis][index] for axis in ordered_axes),
        )
        decision_axis = ordered_axes[0]
        for axis in ordered_axes:
            winner_score = axis_scores[axis][winner_index]
            best_other_score = max(
                score for index, score in enumerate(axis_scores[axis]) if index != winner_index
            )
            if winner_score > best_other_score:
                decision_axis = axis
                break
        winning_gap = axis_scores[decision_axis][winner_index] - max(
            score for index, score in enumerate(axis_scores[decision_axis]) if index != winner_index
        )

    winner_name = names[winner_index]
    winner_code = str(candidates[winner_index].get("region_code", "")) or None
    decision_label = axis_labels.get(decision_axis, decision_axis)
    decision_weight = parsed_weights.get(decision_axis, 0.0)
    conditions = []
    if any(term in preference for term in ("通勤", "勤務地")) and "通勤なし" not in preference:
        conditions.append("実際の勤務地までの経路・所要時間を確認する")
    if any(term in preference for term in ("子育て", "子ども", "子供", "保育")):
        conditions.append("保育施設の空き状況と候補住居からの通園距離を確認する")
    if any(term in preference for term in ("土地", "建てる", "売地")):
        conditions.append("希望する地区の売地を同じ面積で比べ、建築費も別に確認する")
    elif any(term in preference for term in ("家賃", "住宅費", "住居費", "予算")):
        conditions.append("希望する間取り・築年の募集中物件で家賃を比べる")
    if any(term in preference for term in ("医療", "病院", "診療", "受診")):
        conditions.append("必要な診療科と診療時間、候補の家からの通院経路を確認する")
    if any(term in preference for term in ("防災", "災害", "洪水", "浸水")):
        conditions.append("候補物件の住所を自治体のハザードマップで確認する")
    if not conditions:
        conditions.append("候補の住居から、普段の買い物先や必要な施設までの移動を確認する")
    conditions.append("候補に挙がった住居の住所で、町丁目ごとの環境を確認する")

    reasons = [
        f"入力条件で{decision_label}を{decision_weight:.0f}%重視し、{winner_name}を最初に検討する候補にします。"
        if decision_weight > 0
        else f"取得済みの比較根拠を踏まえ、{winner_name}を最初に検討する候補にします。"
    ]
    evidence_reason = _commander_metric_reason(decision_axis, candidates, findings, winner_index)
    if evidence_reason:
        reasons.append(evidence_reason)
    elif not decisive:
        reasons.append(
            "候補間の差は小さいため、まずはこの候補から住居と生活圏を具体的に比べる提案です。"
        )

    tradeoffs: list[str] = []
    for axis in ordered_axes:
        if axis == decision_axis:
            continue
        scores = axis_scores[axis]
        strongest_other = max(
            (index for index in range(len(candidates)) if index != winner_index),
            key=lambda index: scores[index],
        )
        if scores[strongest_other] - scores[winner_index] >= COMMANDER_FALLBACK_AXIS_MARGIN:
            other_reason = _commander_metric_reason(axis, candidates, findings, strongest_other)
            if other_reason:
                tradeoffs.append(f"一方、{other_reason}")
                break
    if not tradeoffs:
        tradeoffs.append(
            "優先条件と他の比較軸の強みが分かれるため、候補物件で実際の生活動線も見てください。"
        )

    fallback_confidence = min(0.65, max(0.25, winning_gap / 25)) if decisive else 0.20
    measured_confidences = [
        _commander_score(candidate_findings.get(decision_axis, {}).get("confidence"))
        for candidate_findings in findings
    ]
    evidence_confidence = min(
        (value if value is not None else 0.4 for value in measured_confidences),
        default=0.4,
    )
    confidence = round(min(fallback_confidence, evidence_confidence), 2)
    return CommanderNarrative(
        recommended_region_code=winner_code,
        proposal_title=f"{decision_label}を重視するなら、{winner_name}を第一候補に",
        recommendation_strength=(
            "hypothesis"
            if payload.get("data_mode") in {"mock", "knowledge_only"}
            else "conditional"
        ),
        candidate_positions=[
            CandidatePosition(
                region_code=str(candidate.get("region_code", "")),
                fit_summary=str(findings[index].get(decision_axis, {}).get("summary"))
                if findings[index].get(decision_axis, {}).get("summary")
                else f"{decision_label}の比較根拠を確認した候補です。",
                selection_condition=(
                    f"{decision_label}を優先する今回の条件での第一候補。"
                    if index == winner_index
                    else "他の希望条件や候補物件の実際の環境を確認して選び分けます。"
                ),
            )
            for index, candidate in enumerate(candidates)
        ],
        summary=(
            f"{decision_label}を重視する今回の希望では、{winner_name}を先に検討する候補にします。"
            + (
                "AIの一般知識からの暫定提案です。地域のデータで理由を確かめましょう。"
                if payload.get("data_mode") == "knowledge_only"
                else (
                    "取得データを使った暫定提案です。"
                    "次の確認を踏まえて、住まいを具体的に比べましょう。"
                )
            )
        ),
        reasons=reasons[:3],
        tradeoffs=tradeoffs[:3],
        next_checks=conditions[:3],
        confidence=confidence,
    )
