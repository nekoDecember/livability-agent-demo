import type { AxisKey, CandidateReport } from "../types";
import { AXIS_LABELS, axisResultFor, commonComparisonAxes, formatScore, normalizeWeights } from "./report";

export function comparisonAxisLabel(candidates: CandidateReport[], axis: AxisKey): string {
  const hasMetric = (codes: string[], label: RegExp) => candidates.every((candidate) => axisResultFor(candidate.report, axis)?.metrics.some((metric) =>
    metric.quality > 0 && metric.direction !== "context_only" && (codes.includes(metric.metric_code) || label.test(metric.label)),
  ));
  if (axis === "convenience" && !hasMetric(["station_density", "railway_lines", "station_passengers", "shinkansen_access"], /駅|鉄道|路線/)) {
    return "買い物・都市活動（交通未評価）";
  }
  if (axis === "housing" && !hasMetric(["transaction_unit_price", "official_land_price"], /家賃|取引単価|地価|住宅価格/)) {
    return "住宅ストック（費用未評価）";
  }
  if (axis === "family" && !hasMetric(["nursery_per_1000_children", "schools_per_1000_children"], /子ども|こども|保育|幼稚園|学校/)) {
    return "医療（子育て未評価）";
  }
  if (axis === "future" && hasMetric(["population_retention_2040", "population_retention_2050", "working_age_retention", "child_population_retention"], /人口.*維持率|人口.*推計/)) {
    return "将来人口の見通し";
  }
  return AXIS_LABELS[axis];
}

export interface AxisVerdict {
  axis: AxisKey;
  leaders: CandidateReport[];
  gap: number;
  nearTie: boolean;
  importance: number;
  confidence: number;
  evidence: string;
}

export interface EvidenceSummary {
  axisVerdicts: AxisVerdict[];
  sharedAxes: AxisKey[];
  reason: string;
  nextCheck: string;
}

export const AXIS_LEAD_THRESHOLD = 3;

function axisEvidence(leader: CandidateReport, follower: CandidateReport, axis: AxisKey): string {
  const first = axisResultFor(leader.report, axis);
  const second = axisResultFor(follower.report, axis);
  if (!first || !second) return "";
  const pairs = first.metrics.flatMap((metric) => {
    const other = second.metrics.find((item) => item.metric_code === metric.metric_code);
    if (!other || metric.quality <= 0 || other.quality <= 0 || metric.direction === "context_only"
      || other.direction === "context_only" || metric.normalized_score <= other.normalized_score) return [];
    return [{ metric, other, lead: metric.normalized_score - other.normalized_score }];
  }).sort((a, b) => b.lead - a.lead);
  const pair = pairs[0];
  if (!pair) return "";
  const leaderName = leader.report.plan.region.name;
  const followerName = follower.report.plan.region.name;
  return `${pair.metric.label}: ${leaderName} ${formatScore(pair.metric.value)}${pair.metric.unit} / ${followerName} ${formatScore(pair.other.value)}${pair.other.unit}`;
}

/** Summarize comparable axis evidence for inspection; this function never chooses a city. */
export function buildEvidenceSummary(
  candidates: CandidateReport[],
  weights: Record<AxisKey, number>,
  preference: string,
): EvidenceSummary {
  const sharedAxes = candidates.length >= 2 ? commonComparisonAxes(candidates) : [];
  const importance = normalizeWeights(weights, sharedAxes);
  const axisVerdicts: AxisVerdict[] = sharedAxes.map((axis) => {
    const byScore = candidates.map((candidate) => ({ candidate, score: axisResultFor(candidate.report, axis)!.score }))
      .sort((a, b) => b.score - a.score);
    const topScore = byScore[0].score;
    const leaders = byScore.filter((item) => topScore - item.score < AXIS_LEAD_THRESHOLD).map((item) => item.candidate);
    const follower = byScore.find((item) => topScore - item.score >= AXIS_LEAD_THRESHOLD);
    return {
      axis,
      leaders,
      gap: byScore.length > 1 ? Math.round((topScore - byScore[1].score) * 10) / 10 : 0,
      nearTie: leaders.length > 1,
      importance: importance[axis],
      confidence: Math.min(...byScore.map((item) => axisResultFor(item.candidate.report, axis)!.confidence)),
      evidence: leaders.length === 1 && follower ? axisEvidence(leaders[0], follower.candidate, axis) : "",
    };
  });

  const reason = candidates.length < 2
    ? "候補を2件以上選ぶと、コマンダーが条件に沿った提案を作ります。"
    : !sharedAxes.length
      ? "候補間で同じ採点可能な指標を確認できません。現在の根拠だけでは推薦しません。"
      : `専門Agentの調査結果のうち${sharedAxes.length}/5視点に共通根拠があります。軸別の点数は補足情報で、提案先はコマンダーが条件と所見を合わせて判断します。`;
  const nextCheck = /通勤|勤務地:/.test(preference.replaceAll("通勤なし", ""))
    ? "候補の住居から勤務地までの経路・所要時間・混雑を同じ時間帯で確認する"
    : /新幹線|鉄道|電車|駅近/.test(preference)
      ? "使う駅までの経路と列車の本数を、実際の住居から確認する"
      : /子育て|子ども|こども/.test(preference)
        ? "保育施設の空き状況、通園先と住居の距離を自治体・施設へ確認する"
        : /家賃|住宅費|住居費|予算/.test(preference)
          ? "同じ間取り・築年・駅距離の募集中物件で実際の住居費を比べる"
          : "候補の物件価格と周辺環境を同じ条件で確認する";
  return { axisVerdicts, sharedAxes, reason, nextCheck };
}
