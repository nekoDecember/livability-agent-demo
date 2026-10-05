import { AXIS_ORDER } from "../types";
import type { AssessmentReport, AxisKey, AxisResult, CandidateReport, MetricEvidence } from "../types";

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

export function metricAvailabilityLabel(metric: MetricEvidence): string {
  if (metric.quality > 0) return metric.value === 0 ? "実測 0" : "取得済み";
  switch (metric.missing_reason) {
    case "not_collected": return "指標未収録";
    case "not_found_for_region": return "この自治体の値なし";
    case "unverified": return "照合未確認";
    default: return "未取得（理由不明）";
  }
}

export function metricAvailabilityDetail(metric: MetricEvidence): string {
  if (metric.quality > 0) return "出典付きの値です";
  switch (metric.missing_reason) {
    case "not_collected": return "今回のデータ集には指標自体が未収録です。元データに存在しないとは限りません。";
    case "not_found_for_region": return "指標は収録されていますが、この自治体の値は今回見つかりませんでした。";
    case "unverified": return "値を照合できず、実測ゼロとして扱っていません。";
    default: return "未取得の理由を特定できません。";
  }
}

export function formatElapsed(milliseconds: number): string {
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(1)} s`;
}

export function axisResultFor(report: AssessmentReport, axis: AxisKey): AxisResult | undefined {
  return report.axis_results.find((result) => result.axis === axis);
}

export function axisUnavailableLabel(report: AssessmentReport, axis: AxisKey): string {
  switch (report.plan.unavailable_axis_reasons?.[axis]) {
    case "not_collected": return "指標未収録";
    case "not_found_for_region": return "この自治体の値なし";
    case "unverified": return "照合未確認";
    default: return report.plan.unavailable_axes?.includes(axis) ? "評価データなし" : "評価対象外";
  }
}

export function commonComparisonAxes(candidates: CandidateReport[]): AxisKey[] {
  if (!candidates.length) return [];
  return AXIS_ORDER.filter((axis) => {
    const signatures = candidates.map((candidate) => {
      const result = axisResultFor(candidate.report, axis);
      if (!result) return "";
      return result.metrics
        .filter((metric) => metric.quality > 0 && metric.direction !== "context_only")
        .map((metric) => metric.metric_code)
        .sort()
        .join("|");
    });
    return signatures[0].length > 0 && signatures.every((value) => value === signatures[0]);
  });
}

export function railAccessUnverified(candidates: CandidateReport[], preference: string): boolean {
  if (/新幹線/.test(preference)) {
    return !candidates.every((candidate) => axisResultFor(candidate.report, "convenience")?.metrics.some(
      (metric) => metric.metric_code === "shinkansen_access" && metric.quality > 0 && metric.direction !== "context_only",
    ));
  }
  if (!/(鉄道|電車|駅近)/.test(preference)) return false;
  return !candidates.every((candidate) => axisResultFor(candidate.report, "convenience")?.metrics.some(
    (metric) => ["railway_lines", "station_density"].includes(metric.metric_code)
      && metric.quality > 0 && metric.direction !== "context_only",
  ));
}

export function leadingComparisonEvidence(
  leader: CandidateReport,
  runnerUp: CandidateReport,
  weights: Record<AxisKey, number>,
  axes: AxisKey[],
): string {
  const normalized = normalizeWeights(weights, axes);
  const advantages = axes.flatMap((axis) => {
    const leading = axisResultFor(leader.report, axis);
    const trailing = axisResultFor(runnerUp.report, axis);
    if (!leading || !trailing || leading.score <= trailing.score) return [];
    return [{ axis, leading, trailing, impact: (leading.score - trailing.score) * normalized[axis] }];
  }).sort((a, b) => b.impact - a.impact);
  const strongest = advantages[0];
  if (!strongest) return "";
  const metricPair = strongest.leading.metrics.flatMap((metric) => {
    const counterpart = strongest.trailing.metrics.find((item) => item.metric_code === metric.metric_code);
    return counterpart && metric.quality > 0 && counterpart.quality > 0
      && metric.direction !== "context_only" && counterpart.direction !== "context_only"
      && metric.normalized_score > counterpart.normalized_score
      ? [{ metric, counterpart, difference: metric.normalized_score - counterpart.normalized_score }]
      : [];
  }).sort((a, b) => b.difference - a.difference)[0];
  const axisText = `${AXIS_LABELS[strongest.axis]}軸の差は${formatScore(strongest.leading.score - strongest.trailing.score)}点`;
  if (!metricPair) return `${leader.report.plan.region.name}は${runnerUp.report.plan.region.name}より${AXIS_LABELS[strongest.axis]}軸で${formatScore(strongest.leading.score - strongest.trailing.score)}点上です。`;
  const scorable = strongest.leading.metrics.filter((metric) => metric.direction !== "context_only");
  const counted = scorable.filter((metric) => metric.quality > 0).length;
  const coverage = counted < scorable.length ? `この軸は${scorable.length}指標中${counted}指標で採点しています。` : "";
  return `${metricPair.metric.label}は${leader.report.plan.region.name}が${formatScore(metricPair.metric.value)}${metricPair.metric.unit}、${runnerUp.report.plan.region.name}が${formatScore(metricPair.counterpart.value)}${metricPair.counterpart.unit}。この指標を含む${axisText}です。${coverage}`;
}

export function normalizeWeights(
  weights: Record<AxisKey, number>,
  enabledAxes: AxisKey[] = AXIS_ORDER,
): Record<AxisKey, number> {
  const enabled = new Set(enabledAxes);
  const total = AXIS_ORDER.reduce(
    (sum, axis) => sum + (enabled.has(axis) ? weights[axis] : 0),
    0,
  );
  if (total <= 0) {
    const equalWeight = enabled.size ? 100 / enabled.size : 0;
    return Object.fromEntries(
      AXIS_ORDER.map((axis) => [axis, enabled.has(axis) ? equalWeight : 0]),
    ) as Record<AxisKey, number>;
  }

  return Object.fromEntries(
    AXIS_ORDER.map((axis) => [
      axis,
      enabled.has(axis) ? (weights[axis] / total) * 100 : 0,
    ]),
  ) as Record<AxisKey, number>;
}

export function reportReferenceDate(report: AssessmentReport): string {
  const dates = new Set(report.axis_results.flatMap((result) =>
    result.metrics
      .filter((metric) => metric.quality > 0)
      .map((metric) => metric.source.reference_date),
  ));
  if (!dates.size) return "—";
  return dates.size === 1 ? [...dates][0] : "指標ごとに異なる";
}

export function uniqueSources(report: AssessmentReport): number {
  return new Set(
    report.axis_results.flatMap((result) =>
      result.metrics
        .filter((metric) => metric.quality > 0)
        .map((metric) => metric.source.source_id),
    ),
  ).size;
}
