import { AXIS_ORDER } from "../types";
import type { AssessmentReport, AxisKey, AxisResult } from "../types";

export { AXIS_ORDER };

export const AXIS_LABELS: Record<AxisKey, string> = {
  convenience: "移動・買い物",
  housing: "住まいコスト",
  family: "医療・子育て",
  safety: "安心・防災",
  future: "まちの将来性",
};

export const AXIS_SHORT_LABELS: Record<AxisKey, string> = {
  convenience: "移動",
  housing: "住まい",
  family: "医療・子育て",
  safety: "防災",
  future: "将来性",
};

export function axisLabel(axis: AxisKey): string {
  return AXIS_LABELS[axis];
}

export function scoreTone(score: number): "high" | "mid" | "low" {
  if (score >= 70) return "high";
  if (score >= 50) return "mid";
  return "low";
}

export function formatScore(score: number): string {
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatElapsed(milliseconds: number): string {
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(1)} s`;
}

export function axisResultFor(report: AssessmentReport, axis: AxisKey): AxisResult | undefined {
  return report.axis_results.find((result) => result.axis === axis);
}

export function normalizeWeights(weights: Record<AxisKey, number>): Record<AxisKey, number> {
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  if (total <= 0) {
    return {
      convenience: 20,
      housing: 20,
      family: 20,
      safety: 20,
      future: 20,
    };
  }

  return Object.fromEntries(
    Object.entries(weights).map(([axis, value]) => [axis, (value / total) * 100]),
  ) as Record<AxisKey, number>;
}

export function weightedScore(
  report: AssessmentReport,
  weights: Record<AxisKey, number>,
): number {
  const normalized = normalizeWeights(weights);
  const enabled = new Set(report.plan.enabled_axes);
  const results = report.axis_results.filter((result) => enabled.has(result.axis));
  if (!results.length) return 0;

  const total = results.reduce(
    (sum, result) => sum + result.score * (normalized[result.axis] / 100),
    0,
  );
  return Math.round(total * 10) / 10;
}

export function reportReferenceDate(report: AssessmentReport): string {
  const dates = report.axis_results.flatMap((result) =>
    result.metrics.map((metric) => metric.source.reference_date),
  );
  return dates.sort().at(-1) ?? "—";
}

export function uniqueSources(report: AssessmentReport): number {
  return new Set(
    report.axis_results.flatMap((result) => result.metrics.map((metric) => metric.source.source_id)),
  ).size;
}
