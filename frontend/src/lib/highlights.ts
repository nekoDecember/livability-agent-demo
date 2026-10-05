import type { AxisKey, CandidateReport } from "../types";
import { AXIS_LABELS, normalizeWeights } from "./report";

export interface CityHighlight {
  axis: AxisKey;
  label: string;
  summary: string;
  evidence: string | null;
  source: string | null;
  contextOnly: boolean;
}

/** Only describe a strength when the assessment supports it; never turn missing data into a selling point. */
export function cityHighlights(candidate: CandidateReport, weights: Record<AxisKey, number>): CityHighlight[] {
  const results = candidate.report.axis_results.filter((result) => result.score >= 50 && result.confidence >= 0.4 && result.narrative.summary.trim());
  const normalized = normalizeWeights(weights, candidate.report.plan.enabled_axes);
  return results
    .sort((a, b) =>
      (normalized[b.axis] * b.score + b.confidence * 100) -
      (normalized[a.axis] * a.score + a.confidence * 100),
    )
    .slice(0, 3)
    .map((result) => {
      const available = result.metrics.filter((item) => item.quality > 0 && !item.is_mock);
      const metric = available.find((item) => item.direction === "context_only" && (
        result.narrative.summary.includes(item.label) ||
        (item.metric_code === "takasaki_shinkansen_connections" && result.narrative.summary.includes("新幹線"))
      )) ??
        [...available]
          .filter((item) => item.direction !== "context_only")
          .sort((a, b) => b.normalized_score - a.normalized_score)[0];
      return {
        axis: result.axis,
        label: AXIS_LABELS[result.axis],
        summary: result.narrative.summary,
        evidence: metric ? `${metric.label} ${metric.value} ${metric.unit}` : null,
        source: metric ? `${metric.source.source_name} / ${metric.source.reference_date}` : null,
        contextOnly: metric?.direction === "context_only",
      };
    });
}
