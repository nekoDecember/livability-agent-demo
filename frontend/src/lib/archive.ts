import type { AxisKey, CandidateComparison, CandidateReport, ComparisonMethods, KnowledgeBaseline } from "../types";
import { AXIS_ORDER, axisResultFor, axisUnavailableLabel, formatScore, metricAvailabilityLabel, normalizeWeights } from "./report";
import { renderComparisonHtml } from "./reportHtml";
import { cityHighlights } from "./highlights";
import { isDemoCandidate, isMeasuredCandidate, proposalEvidence, proposalHeading, comparisonChange, baselineIsOffline } from "./proposal";
import { renderPresentationHtml } from "./presentationHtml";
import { buildEvidenceSummary, comparisonAxisLabel } from "./decision";
import { markdownMethodSection } from "./methodComparison";

export const ARCHIVE_KEY = "livability:reports:v1";
export interface SavedComparison {
  id: string;
  savedAt: string;
  preference: string;
  weights: Record<AxisKey, number>;
  candidates: CandidateReport[];
  commander?: CandidateComparison;
  baseline?: KnowledgeBaseline;
  comparisonMethods?: ComparisonMethods;
  markdown: string;
  html: string;
}
const modeLabel = (c: CandidateReport) => {
  if (c.source === "web" || c.report.plan.data_mode === "web_search" || c.report.research_context?.method === "web_search") {
    return c.report.research_context?.status === "offline" ? "Web検索未実行" : "Web検索型";
  }
  if (c.report.research_context?.method === "data_context") {
    return c.report.research_context.status === "verified" ? "データ収集型" : "データ未取得";
  }
  if (c.source === "knowledge" || c.report.plan.data_mode === "knowledge_only") {
    return c.report.execution_steps.some(step => step.status === "fallback")
      ? "オフライン代替（外部データ・LLMなし）"
      : "LLM知識のみ（最新性なし）";
  }
  if (c.source === "demo" || c.report.plan.data_mode === "mock" || c.report.axis_results.some(a => a.metrics.some(m => m.is_mock))) return "デモ（意思決定不可）";
  if (c.report.plan.data_mode === "open_data") return "公式公開データ";
  return "外部データ";
};
const hasMeasuredData = isMeasuredCandidate;
const cell = (value: string) => value.replaceAll("|", "\\|").replaceAll("\n", " ");

function createArchiveId(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    try {
      return cryptoApi.randomUUID();
    } catch {
      // randomUUID is restricted to secure contexts in some browsers.
    }
  }

  const bytes = new Uint8Array(16);
  let hasSecureRandom = false;
  try {
    if (typeof cryptoApi?.getRandomValues === "function") {
      cryptoApi.getRandomValues(bytes);
      hasSecureRandom = true;
    }
  } catch {
    // Fall through to a local identifier if the browser blocks Web Crypto.
  }
  if (!hasSecureRandom) {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createComparison(candidates: CandidateReport[], weights: Record<AxisKey, number>, preference: string, commander?: CandidateComparison, baseline?: KnowledgeBaseline, comparisonMethods?: ComparisonMethods): SavedComparison {
  const decision = buildEvidenceSummary(candidates, weights, preference);
  const sharedAxes = decision.sharedAxes;
  const sharedWeights = normalizeWeights(weights, sharedAxes);
  const winner = commander?.recommended_region_code
    ? candidates.find((candidate) => candidate.report.plan.region.municipality_code === commander.recommended_region_code)
    : undefined;
  const featured = winner;
  const commuteUnverified = /通勤|勤務地:/.test(preference.replaceAll("通勤なし", ""));
  const measured = candidates.length >= 2 && candidates.every(hasMeasuredData);
  const conclusion = commander
    ? `${commander.narrative.summary} 理由: ${commander.narrative.reasons.join(" / ")}。トレードオフ: ${commander.narrative.tradeoffs.join(" / ")}。次に確認: ${commander.narrative.next_checks.join(" / ")}`
    : "司令塔の統合提案はまだ作成されていません。調査根拠だけでは候補を一つに決めません。";
  const limitation = candidates.some(isDemoCandidate)
    ? "デモ値のため、実在地域の意思決定には使用できません。"
    : !commander
      ? "司令塔の統合結果がないため、候補の推薦は表示していません。"
      : !winner
        ? "今回の条件で一つに絞れる根拠がないため、順位をつけていません。"
    : !measured
      ? `${modeLabel(winner)}のため、比較方法の例です。実在地域の判断には一次情報を確認してください。`
      : commuteUnverified
        ? "勤務地までの実際の経路・所要時間は未測定です。この選択が通勤の優劣を証明するものではありません。"
        : "司令塔は暮らしの条件・専門Agentの所見・比較可能な根拠をまとめています。軸別の数値は提案の補足で、単純合計で候補を選んでいません。";
  const sections = [`# ${comparisonMethods ? "都市選びの2方式比較" : "都市選びのご提案"}`, `条件: ${preference || "指定なし"}`, comparisonMethods ? "## 選択した方式の既存統合回答（補足）" : "## 結論",
    proposalHeading(candidates, commander),
    conclusion,
    limitation,
    comparisonMethods ? "## 軸別指標の補足" : "## データで確認できた違い",
    ...proposalEvidence(candidates, weights, commander).slice(0, 6).map(item => `- ${item.label}: ${item.verdict}。${item.meaning}\n  - 物件選びでは: ${item.nextCheck}\n  - 数字・出典: ${item.metricLabel} / ${item.comparison} / ${item.difference} / ${item.source}`),
    "## 各都市を選ぶ条件",
    ...(commander?.narrative.candidate_positions ?? []).map(item => `- ${candidates.find(c => c.report.plan.region.municipality_code === item.region_code)?.report.plan.region.name ?? item.region_code}: ${item.fit_summary} / 選ぶ条件: ${item.selection_condition}`),
    ...(baseline ? ["## データなし／ありの比較", comparisonChange({ candidates, commander, baseline, weights, preference }), `${baselineIsOffline(baseline) ? "オフライン代替を含む" : "LLM知識のみ"}: ${baseline.commander.narrative.summary}`, ...baseline.commander.narrative.reasons.map(text => `- データなしの理由: ${text}`), ...baseline.commander.narrative.next_checks.map(text => `- データなしの次の確認: ${text}`), "同じ都市・希望・優先度とコマンダーで比較。調査段階の構成が異なるため厳密なデータ効果の実験ではありません。"] : []),
    "## この街の魅力",
    featured ? `${featured.report.plan.region.name}の暮らしを、今回の条件から見る。` : "比較できる指標を確認してください。",
    ...(featured ? cityHighlights(featured, weights).flatMap((highlight) => [
      `### ${highlight.label}`,
      highlight.summary,
      highlight.evidence ? `${highlight.contextOnly ? "街の背景情報（採点対象外）" : "参考指標"}: ${highlight.evidence} / ${highlight.source}` : "数値根拠は未取得。評価結果に基づく見立てです。",
    ]) : []),
    "## 専門Agentの調査根拠（補足）",
    "以下は司令塔の提案を確かめる補足です。軸別スコアの合計や優劣順で候補を選びません。",
    "| 視点 | 司令塔へ伝えた優先度 | " + candidates.map(c => cell(c.report.plan.region.name)).join(" | ") + " |",
    "|---|---:|" + candidates.map(() => "---:").join("|") + "|"];
  for (const axis of AXIS_ORDER) sections.push(`| ${comparisonAxisLabel(candidates, axis)} | ${sharedAxes.includes(axis) ? `${sharedWeights[axis].toFixed(1)}%` : "—"} | ${candidates.map(c => {
    const result = axisResultFor(c.report, axis);
    return sharedAxes.includes(axis) && result
      ? `軸内参考スコア ${result.score} / 根拠確度 ${Math.round(result.confidence * 100)}%`
      : result ? "共通指標なし" : axisUnavailableLabel(c.report, axis);
  }).join(" | ")} |`);
  for (const c of candidates) {
    const r = c.report;
    sections.push("", `## 根拠: ${r.plan.region.name}`, `生成日時: ${r.generated_at}`);
    if (!hasMeasuredData(c)) sections.push(`> ${modeLabel(c)}。実在地域の意思決定には一次情報の確認が必要です。`);
    sections.push("### 根拠・出典");
    for (const axis of r.axis_results) {
      sections.push(`#### ${comparisonAxisLabel(candidates, axis.axis)}`);
      for (const m of axis.metrics) sections.push(
        m.quality > 0
          ? `- ${m.label}: ${m.value} ${m.unit}${m.direction === "context_only" ? "（背景情報・採点対象外）" : ""} / ${m.source.source_name} / ${m.source.reference_date} / ${m.source.url} / ${m.source.commercial_use_note}`
          : `- ${m.label}: ${metricAvailabilityLabel(m)}（採点対象外）`,
      );
    }
  }
  const methodSections = comparisonMethods ? ["## 2方式の回答比較", ...markdownMethodSection("data_context", comparisonMethods.data_context), ...markdownMethodSection("web_search", comparisonMethods.web_search)] : [];
  const markdownSections = comparisonMethods
    ? [
      ...sections.slice(0, 2),
      ...methodSections,
      "",
      "<details>",
      "<summary>都市ごとの補足情報・軸別指標を見る</summary>",
      "",
      ...sections.slice(2),
      "",
      "</details>",
    ]
    : sections;
  const saved = {
    id: createArchiveId(),
    savedAt: new Date().toISOString(),
    preference,
    weights: { ...weights },
    candidates,
    commander,
    baseline,
    comparisonMethods,
    markdown: markdownSections.join("\n").replace(/^(#{1,4} .+)$/gm, "\n$1\n") + "\n",
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
        if (c.source !== "api" && c.source !== "demo" && c.source !== "knowledge" && c.source !== "web") return false;
        const r = c.report;
        const legacyConfidence = (r as unknown as Record<string, unknown>).overall_confidence;
        if (typeof r.report_id !== "string" || typeof r.plan.region.name !== "string" || !Array.isArray(r.plan.enabled_axes) || !Number.isFinite(r.research_confidence ?? legacyConfidence)) return false;
        if (!Array.isArray(r.execution_steps) || !Array.isArray(r.axis_results)) return false;
        for (const a of r.axis_results) {
          if (!AXIS_ORDER.includes(a.axis) || !Number.isFinite(a.score) || !Number.isFinite(a.confidence) || typeof a.narrative.summary !== "string") return false;
          if (![a.narrative.strengths, a.narrative.cautions].every(v => Array.isArray(v) && v.every(t => typeof t === "string"))) return false;
          for (const m of a.metrics) if (typeof m.source.reference_date !== "string" || typeof m.source.url !== "string" || typeof m.label !== "string") return false;
        }
      }
      if (item.commander !== undefined && (!item.commander.narrative || !Array.isArray(item.commander.narrative.reasons))) return false;
      if (item.comparisonMethods !== undefined) {
        for (const method of [item.comparisonMethods.data_context, item.comparisonMethods.web_search]) {
          if (method === undefined) continue;
          if ((method.status !== "completed" && method.status !== "failed") || !Array.isArray(method.candidates) || method.candidates.length > 4) return false;
          if (method.candidates.some((candidate: CandidateReport) => !candidate?.report?.plan?.region?.municipality_code)) return false;
          if (method.comparison !== undefined && !method.comparison.narrative) return false;
        }
      }
      if (item.baseline !== undefined) {
        const baseline = item.baseline;
        if (!baseline.commander?.narrative || !Array.isArray(baseline.commander.narrative.reasons) || !Array.isArray(baseline.commander.narrative.next_checks) || !Array.isArray(baseline.candidates)) return false;
        if (baseline.candidates.length !== item.candidates.length || baseline.candidates.some((c: CandidateReport, index: number) => c.report.plan.data_mode !== "knowledge_only" || c.report.plan.region.municipality_code !== item.candidates[index].report.plan.region.municipality_code)) return false;
      }
      createComparison(item.candidates, item.weights, item.preference, item.commander, item.baseline);
      return true;
    } catch { return false; }
  }).slice(0, 10).map((item) => {
    const candidates = item.candidates.map((candidate) => {
      const legacyConfidence = (candidate.report as unknown as Record<string, unknown>).overall_confidence;
      return {
        ...candidate,
        report: {
          ...candidate.report,
          research_confidence: candidate.report.research_confidence
            ?? (typeof legacyConfidence === "number" ? legacyConfidence : 0.5),
        },
      };
    });
    const restored = { ...item, candidates };
    return { ...restored, html: renderComparisonHtml(restored) };
  });
}

export function downloadComparison(comparison: SavedComparison, format: "md" | "json" | "html" | "presentation") {
  const body = format === "presentation" ? renderPresentationHtml(comparison) : format === "md" ? comparison.markdown : format === "html" ? comparison.html : JSON.stringify(comparison, null, 2);
  const blob = new Blob([body], { type: format === "md" ? "text/markdown;charset=utf-8" : (format === "html" || format === "presentation") ? "text/html;charset=utf-8" : "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `livability-${comparison.savedAt.slice(0, 10)}-${comparison.id.slice(0, 8)}${format === "presentation" ? "-presentation.html" : `.${format}`}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
