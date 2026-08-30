from __future__ import annotations

from .models import AXIS_LABELS, AssessmentPlan, AxisEvidence, AxisNarrative, AxisResult


def score_axis(evidence: AxisEvidence, narrative: AxisNarrative) -> AxisResult:
    available = [metric for metric in evidence.metrics if metric.quality > 0]
    total_weight = sum(metric.weight for metric in available)
    if total_weight <= 0:
        raise ValueError(f"No scorable metrics for axis={evidence.axis.value}")

    score = sum(metric.normalized_score * metric.weight for metric in available) / total_weight
    confidence = sum(metric.quality * metric.weight for metric in available) / total_weight
    if evidence.data_mode == "mock":
        confidence = min(confidence, 0.50)

    return AxisResult(
        axis=evidence.axis,
        label=AXIS_LABELS[evidence.axis],
        score=round(score, 1),
        confidence=round(confidence, 2),
        narrative=narrative,
        metrics=evidence.metrics,
        api_calls=evidence.api_calls,
        elapsed_ms=evidence.elapsed_ms,
    )


def score_overall(plan: AssessmentPlan, axis_results: list[AxisResult]) -> tuple[float, float]:
    by_axis = {result.axis: result for result in axis_results}
    usable = [(axis, weight) for axis, weight in plan.weights.items() if axis in by_axis]
    total_weight = sum(weight for _, weight in usable)
    if total_weight <= 0:
        raise ValueError("No axis results available for overall scoring.")

    overall = sum(by_axis[axis].score * weight for axis, weight in usable) / total_weight
    confidence = sum(by_axis[axis].confidence * weight for axis, weight in usable) / total_weight
    return round(overall, 1), round(confidence, 2)
