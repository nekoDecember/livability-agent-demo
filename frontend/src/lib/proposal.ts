import type { AxisKey, CandidateComparison, CandidateReport, KnowledgeBaseline } from "../types";
import { AXIS_ORDER, axisResultFor, formatScore } from "./report";
import { customerEvidence } from "./customerEvidence";

export function isMeasuredCandidate(candidate: CandidateReport): boolean {
  return candidate.source === "api"
    && ["open_data", "government_api"].includes(candidate.report.plan.data_mode)
    && !candidate.report.axis_results.some(result => result.metrics.some(metric => metric.is_mock));
}

export function proposalHeading(candidates: CandidateReport[], commander?: CandidateComparison): string {
  if (candidates.length < 2) return "候補を2件選んでください。";
  const winner = candidates.find(candidate => candidate.report.plan.region.municipality_code === commander?.recommended_region_code);
  if (!commander) return "あなたに合う都市の提案をまとめています";
  if (!winner) return "今回の希望では、候補を一つに絞りません";
  if (!candidates.every(isMeasuredCandidate)) return `調査前の仮説：${winner.report.plan.region.name}を第一候補に`;
  return commander.narrative.proposal_title || `今回の希望には、${winner.report.plan.region.name}を第一候補に提案します`;
}

export function proposalStrength(candidates: CandidateReport[], commander?: CandidateComparison): string {
  if (!commander) return "提案作成中";
  if (!commander.recommended_region_code) return "選び分けを保留";
  if (!candidates.every(isMeasuredCandidate)) return "調査前の仮説";
  if (commander.used_fallback) return "取得データによる暫定提案";
  return commander.narrative.recommendation_strength === "recommended" ? "根拠に基づく推薦" : "条件付きの推薦";
}

export interface ProposalEvidence {
  code: string;
  axis: AxisKey;
  label: string;
  metricLabel: string;
  verdict: string;
  meaning: string;
  nextCheck: string;
  difference: string;
  comparison: string;
  source: string;
  urls: string[];
  decisive: boolean;
}

const measuredValue = (value: number): string => new Intl.NumberFormat("ja-JP", {
  useGrouping: false, maximumSignificantDigits: 7,
}).format(value);

/** Facts only: missing values, learned scores, mixed years/units and mock values cannot be proof. */
export function proposalEvidence(candidates: CandidateReport[], weights: Record<AxisKey, number>, commander?: CandidateComparison): ProposalEvidence[] {
  if (candidates.length < 2 || !candidates.every(isMeasuredCandidate)) return [];
  const supportingCodes = commander?.narrative.supporting_metric_codes ?? [];
  const supporting = new Set(supportingCodes);
  return AXIS_ORDER.flatMap(axis => {
    const first = axisResultFor(candidates[0].report, axis);
    const seen = new Set<string>();
    return (first?.metrics ?? []).flatMap(metric => {
      if (seen.has(metric.metric_code)) return [];
      seen.add(metric.metric_code);
      const metrics = candidates.map(candidate => axisResultFor(candidate.report, axis)?.metrics.find(item => item.metric_code === metric.metric_code));
      if (metrics.some(item => !item || item.quality <= 0 || item.is_mock || item.direction === "context_only"
        || !Number.isFinite(item.value) || item.unit !== metric.unit || item.direction !== metric.direction
        || item.source.reference_date !== metric.source.reference_date || item.source.source_id !== metric.source.source_id)) return [];
      const values = metrics.map(item => item!.value);
      const low = Math.min(...values);
      const high = Math.max(...values);
      const target = metric.direction === "lower_is_better" ? low : high;
      const names = candidates.filter((_, index) => values[index] === target).map(item => item.report.plan.region.name).join("・");
      const difference = high - low;
      const percent = low > 0 && difference > 0 ? `最大値と最小値の差：最小値に対して${formatScore(difference / low * 100)}%` : "";
      const translated = customerEvidence(metric.metric_code, metric.label, names, low === high, metric.direction === "lower_is_better");
      return [{
        code: metric.metric_code, axis, ...translated, metricLabel: metric.label, difference: percent,
        comparison: candidates.map((candidate, index) => `${candidate.report.plan.region.name} ${measuredValue(values[index])}${metric.unit}`).join(" / "),
        source: `${metric.source.source_name} / ${metric.source.reference_date}`,
        urls: [...new Set(metrics.map(item => item!.source.url).filter(url => /^https?:\/\//.test(url)))],
        decisive: supporting.has(metric.metric_code),
      }];
    });
  }).sort((a, b) => Number(b.decisive) - Number(a.decisive)
    || (a.decisive && b.decisive ? supportingCodes.indexOf(a.code) - supportingCodes.indexOf(b.code) : 0)
    || weights[b.axis] - weights[a.axis]);
}

export interface ProposalInput {
  preference: string;
  weights: Record<AxisKey, number>;
  candidates: CandidateReport[];
  commander?: CandidateComparison;
  baseline?: KnowledgeBaseline;
}

export function baselineIsOffline(baseline: KnowledgeBaseline): boolean {
  return baseline.commander.used_fallback || baseline.candidates.some(candidate => candidate.report.execution_steps.some(step => step.status === "fallback"));
}

export function comparisonChange(input: ProposalInput): string {
  if (!input.baseline || !input.commander) return "";
  const before = input.baseline.commander.recommended_region_code;
  const after = input.commander.recommended_region_code;
  const name = (code: string | null) => input.candidates.find(candidate => candidate.report.plan.region.municipality_code === code)?.report.plan.region.name ?? "選び分けを保留";
  if (before !== after) return `提案の変化：${name(before)} → ${name(after)}`;
  return after ? `推薦先は${name(after)}で一致。理由と確認事項の違いをご覧ください` : "どちらも選び分けを保留。判断できた論点の違いをご覧ください";
}

export function candidatePosition(input: ProposalInput, candidate: CandidateReport): { fit: string; condition: string } {
  const position = input.commander?.narrative.candidate_positions?.find(item => item.region_code === candidate.report.plan.region.municipality_code);
  const result = [...candidate.report.axis_results].sort((a, b) => input.weights[b.axis] - input.weights[a.axis])[0];
  return {
    fit: position?.fit_summary ?? result?.narrative.summary ?? "今回の希望に沿うデータを追加確認する候補です。",
    condition: position?.selection_condition ?? "希望する物件と生活圏を確認して選び分けます。",
  };
}

export interface ProposalSlide { title: string; lead: string; items: string[] }

export function proposalSlides(input: ProposalInput): ProposalSlide[] {
  const narrative = input.commander?.narrative;
  const evidence = proposalEvidence(input.candidates, input.weights, input.commander).slice(0, 4);
  const slides = [
    { title: proposalHeading(input.candidates, input.commander), lead: narrative?.summary ?? "提案を作成中です。", items: [`あなたの希望：${input.preference || "指定なし"}`, proposalStrength(input.candidates, input.commander)] },
    { title: "この都市を選ぶ理由", lead: "今回の希望に対する決め手", items: narrative?.reasons ?? [] },
    { title: "暮らしに関わる、確かめられた違い", lead: evidence.length ? "市全体の数字を、住まい選びの手掛かりに。" : "今回は、同じ基準で比べられる数字がまだ揃っていません。", items: evidence.map(item => `${item.label}：${item.verdict}\n${item.meaning}\n${item.comparison}（${item.source}）`) },
    { title: "他の候補を選ぶなら", lead: "希望が変わったときの選び分け", items: input.candidates.map(candidate => { const position = candidatePosition(input, candidate); return `${candidate.report.plan.region.name}：${position.fit}\n選ぶ条件：${position.condition}`; }) },
    { title: "次の一歩", lead: "提案を具体的な住まい選びにつなげる", items: [...(narrative?.tradeoffs ?? []).map(text => `受け入れる点：${text}`), ...(narrative?.next_checks ?? []).map(text => `確認すること：${text}`)] },
  ];
  if (input.baseline) slides.splice(4, 0, {
    title: "街の印象から、確かめた提案へ", lead: comparisonChange(input),
    items: [`調べる前${baselineIsOffline(input.baseline) ? "（代替処理を含む）" : "（AIの一般知識）"}：${input.baseline.commander.narrative.summary}`, `データを確かめた後：${narrative?.summary ?? ""}`, ...evidence.slice(0, 2).map(item => `確かめられたこと：${item.verdict}。${item.comparison}`)],
  });
  return slides;
}
