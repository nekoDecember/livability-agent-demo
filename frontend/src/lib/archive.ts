import type { AxisKey, CandidateReport } from "../types";
import { AXIS_LABELS, AXIS_ORDER, axisResultFor, normalizeWeights, weightedScore } from "./report";
import { renderComparisonHtml } from "./reportHtml";

export const ARCHIVE_KEY = "livability:reports:v1";
export interface SavedComparison {
  id: string;
  savedAt: string;
  preference: string;
  weights: Record<AxisKey, number>;
  candidates: CandidateReport[];
  markdown: string;
  html: string;
}
const modeLabel = (c: CandidateReport) => {
  if (c.source === "knowledge" || c.report.plan.data_mode === "knowledge_only") {
    return c.report.execution_steps.some(step => step.status === "fallback")
      ? "オフライン代替（外部データ・LLMなし）"
      : "LLM知識のみ（最新性なし）";
  }
  if (c.source === "demo" || c.report.plan.data_mode === "mock" || c.report.axis_results.some(a => a.metrics.some(m => m.is_mock))) return "デモ（意思決定不可）";
  return "外部データ";
};
const cell = (value: string) => value.replaceAll("|", "\\|").replaceAll("\n", " ");

export function createComparison(candidates: CandidateReport[], weights: Record<AxisKey, number>, preference: string): SavedComparison {
  const normalized = normalizeWeights(weights);
  const ranking = [...candidates].sort((a, b) => weightedScore(b.report, weights) - weightedScore(a.report, weights));
  const winner = ranking[0];
  const runnerUp = ranking[1];
  const winnerScore = winner ? weightedScore(winner.report, weights) : 0;
  const runnerUpScore = runnerUp ? weightedScore(runnerUp.report, weights) : 0;
  const gap = Math.round((winnerScore - runnerUpScore) * 10) / 10;
  const conclusion = winner
    ? `今回の条件では、${winner.report.plan.region.name}を第一候補にします。${runnerUp ? `次点の${runnerUp.report.plan.region.name}との差は${gap}点です。` : ""}`
    : "候補地を入力して比較を実行してください。";
  const limitation = winner && modeLabel(winner).startsWith("デモ")
    ? "デモ値のため、実在地域の意思決定には使用できません。"
    : winner && modeLabel(winner) !== "外部データ"
      ? `${modeLabel(winner)}のため、この結論は方向性の仮説です。契約前の意思決定には一次情報を確認してください。`
      : "点数だけで判断せず、各候補の根拠と注意点を確認してください。";
  const sections = ["# 住みやすさ比較レポート", `条件: ${preference || "指定なし"}`, "## 結論",
    conclusion,
    limitation,
    "## 比較表", "| 候補地 | 総合点 | データ |", "|---|---:|---|"];
  for (const c of ranking) sections.push(`| ${cell(c.report.plan.region.name)} | ${weightedScore(c.report, weights)} | ${modeLabel(c)} |`);
  sections.push("", "## 評価軸", "| 評価軸 | 表示上の重み | " + candidates.map(c => cell(c.report.plan.region.name)).join(" | ") + " |", "|---|---:|" + candidates.map(() => "---:").join("|") + "|");
  for (const axis of AXIS_ORDER) sections.push(`| ${AXIS_LABELS[axis]} | ${normalized[axis].toFixed(1)}% | ${candidates.map(c => axisResultFor(c.report, axis)?.score ?? "未実行").join(" | ")} |`);
  for (const c of candidates) {
    const r = c.report;
    sections.push("", `## ${r.plan.region.name}`, `生成日時: ${r.generated_at}`, r.narrative.executive_summary);
    if (modeLabel(c) !== "外部データ") sections.push(`> ${modeLabel(c)}。実在地域の意思決定には一次情報の確認が必要です。`);
    sections.push("### 注意点", ...[...r.narrative.cautions, ...r.disclaimers].map(t => `- ${t}`));
    sections.push("### 根拠・出典");
    for (const axis of r.axis_results) {
      sections.push(`#### ${AXIS_LABELS[axis.axis]}`, axis.narrative.summary, ...axis.narrative.cautions.map(t => `- 注意: ${t}`));
      for (const m of axis.metrics) sections.push(`- ${m.label}: ${m.value} ${m.unit} / ${m.source.source_name} / ${m.source.reference_date} / ${m.source.url}`);
    }
    if (c.markdown) sections.push("### APIの元レポート", c.markdown);
  }
  const saved = {
    id: crypto.randomUUID(),
    savedAt: new Date().toISOString(),
    preference,
    weights: { ...weights },
    candidates,
    markdown: sections.join("\n").replace(/^(#{1,4} .+)$/gm, "\n$1\n") + "\n",
  };
  return { ...saved, html: renderComparisonHtml(saved) };
}

export function readArchive(storage: Pick<Storage, "getItem">): SavedComparison[] {
  const value: unknown = JSON.parse(storage.getItem(ARCHIVE_KEY) ?? "[]");
  if (!Array.isArray(value)) throw new Error("保存履歴の形式が不正です。");
  return value.filter((item): item is SavedComparison => {
    try {
      if (!item || typeof item.id !== "string" || typeof item.savedAt !== "string" || typeof item.markdown !== "string" || typeof item.preference !== "string" || !Array.isArray(item.candidates) || !item.candidates.length || item.candidates.length > 4) return false;
      if (!AXIS_ORDER.every(axis => Number.isFinite(item.weights?.[axis]) && item.weights[axis] >= 0)) return false;
      // Exercise the fields used by the report viewer before restoring a snapshot.
      for (const c of item.candidates) {
        if (!Array.isArray(c.progress) || !c.progress.every((p: unknown) => typeof p === "string")) return false;
        if (c.source !== "api" && c.source !== "demo" && c.source !== "knowledge") return false;
        const r = c.report;
        if (typeof r.report_id !== "string" || typeof r.plan.region.name !== "string" || !Array.isArray(r.plan.enabled_axes) || !Number.isFinite(r.overall_confidence)) return false;
        if (!Array.isArray(r.execution_steps) || !Array.isArray(r.axis_results)) return false;
        for (const a of r.axis_results) {
          if (!AXIS_ORDER.includes(a.axis) || !Number.isFinite(a.score) || !Number.isFinite(a.confidence) || typeof a.narrative.summary !== "string") return false;
          if (![a.narrative.strengths, a.narrative.cautions].every(v => Array.isArray(v) && v.every(t => typeof t === "string"))) return false;
          for (const m of a.metrics) if (typeof m.source.reference_date !== "string" || typeof m.source.url !== "string" || typeof m.label !== "string") return false;
        }
      }
      createComparison(item.candidates, item.weights, item.preference);
      return true;
    } catch { return false; }
  }).slice(0, 10).map((item) => item.html ? item : { ...item, html: renderComparisonHtml(item) });
}

export function downloadComparison(comparison: SavedComparison, format: "md" | "json" | "html") {
  const body = format === "md" ? comparison.markdown : format === "html" ? comparison.html : JSON.stringify(comparison, null, 2);
  const blob = new Blob([body], { type: format === "md" ? "text/markdown;charset=utf-8" : format === "html" ? "text/html;charset=utf-8" : "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `livability-${comparison.savedAt.slice(0, 10)}-${comparison.id.slice(0, 8)}.${format}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
