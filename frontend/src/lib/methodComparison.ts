import type { ComparisonMethodRun, ResearchContext, ResearchSource } from "../types";

export type ComparisonMethodKey = "data_context" | "web_search";

export function methodLabel(method: ComparisonMethodKey): string {
  return method === "data_context" ? "データ収集型" : "Web検索型";
}

export function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export function researchContextOf(run?: ComparisonMethodRun): ResearchContext[] {
  return (run?.candidates ?? []).flatMap((candidate) => candidate.report.research_context ? [candidate.report.research_context] : []);
}

export function methodStatusLabel(method: ComparisonMethodKey, run?: ComparisonMethodRun): string {
  if (!run || run.status === "failed") return "この方式は未完了";
  const contexts = researchContextOf(run);
  if (method === "web_search") {
    if (contexts.length && contexts.every((context) => context.status === "offline")) return "Web検索未実行";
    if (contexts.some((context) => context.status === "searched")) return "検索済み";
    return contexts.some((context) => context.status === "failed") ? "この方式は未完了" : "Web検索結果を受信";
  }
  if (contexts.some((context) => context.status === "failed")) return "一部未完了";
  if (contexts.length && contexts.every((context) => context.status === "verified")) return "確認済み";
  if (contexts.some((context) => context.status === "verified")) return "一部確認済み";
  if (contexts.length && contexts.every((context) => context.status === "offline")) return "データ未取得";
  return "データを受信";
}

export function controlledFields(run?: ComparisonMethodRun): string[] {
  return uniqueStrings(researchContextOf(run).flatMap((context) => context.controlled_fields ?? []));
}

export function limitations(run?: ComparisonMethodRun): string[] {
  return uniqueStrings(researchContextOf(run).flatMap((context) => context.limitations ?? []));
}

export function searchRoundCount(run?: ComparisonMethodRun): { rounds: number; maxRounds: number } {
  const contexts = researchContextOf(run);
  return {
    rounds: Math.max(0, ...contexts.map((context) => Number.isFinite(context.search_rounds) ? context.search_rounds : 0)),
    maxRounds: Math.max(0, ...contexts.map((context) => Number.isFinite(context.max_search_rounds) ? context.max_search_rounds : 0)),
  };
}

export function researchSources(context?: ResearchContext): ResearchSource[] {
  return (context?.sources ?? []).map((source) => ({
    url: typeof source?.url === "string" ? source.url : "",
    title: typeof source?.title === "string" ? source.title : "",
  }));
}

export function methodSources(run?: ComparisonMethodRun): ResearchSource[] {
  const first = researchContextOf(run)[0];
  if (first) return researchSources(first);
  return [];
}

export function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter((value) => typeof value === "string" && value.trim()))];
}

export function escapeHtml(value: unknown): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function citationHtml(text: string, sources: ResearchSource[]): string {
  return text.split(/(\[\d+\])/g).map((part) => {
    const match = /^\[(\d+)\]$/.exec(part);
    if (!match) return escapeHtml(part);
    const source = sources[Number(match[1]) - 1];
    const url = source ? safeHttpUrl(source.url) : null;
    return url
      ? `<a class="inline-citation" href="${escapeHtml(url)}" target="_blank" rel="noreferrer" aria-label="出典 ${match[1]}: ${escapeHtml(source.title)}">${part}</a>`
      : escapeHtml(part);
  }).join("");
}

export function markdownMethodSection(method: ComparisonMethodKey, run?: ComparisonMethodRun): string[] {
  const lines = [`### ${methodLabel(method)}`, `状態: ${methodStatusLabel(method, run)}`];
  if (!run) return [...lines, "この方式の結果はありません。"];
  if (run.error) lines.push(`未完了の理由: ${run.error}`);

  const fields = controlledFields(run);
  lines.push(`指定・検証できた前提: ${fields.length ? fields.join("、") : "記録なし"}`);
  const constraints = limitations(run);
  if (constraints.length) lines.push(`制約: ${constraints.join("。")}`);

  if (method === "web_search") {
    const rounds = searchRoundCount(run);
    lines.push(`検索回数: ${rounds.rounds}${rounds.maxRounds ? ` / 最大${rounds.maxRounds}回` : ""}`);
  }

  const sources = methodSources(run);
  const summary = run.comparison?.narrative.summary;
  if (summary) {
    lines.push("回答:", summary);
    for (const reason of run.comparison?.narrative.reasons ?? []) lines.push(`- ${reason}`);
    if (run.comparison?.narrative.tradeoffs.length) lines.push("確認したい違い:", ...run.comparison.narrative.tradeoffs.map((item) => `- ${item}`));
    if (run.comparison?.narrative.next_checks.length) lines.push("次に確認すること:", ...run.comparison.narrative.next_checks.map((item) => `- ${item}`));
  }

  for (const candidate of run.candidates) {
    const report = candidate.report;
    const context = report.research_context;
    lines.push(`#### ${report.plan.region.name}`, report.narrative.executive_summary);
    for (const strength of report.narrative.strengths) lines.push(`- ${strength}`);
    for (const caution of report.narrative.cautions) lines.push(`- 注意: ${caution}`);
    if (method === "web_search" && context) {
      for (const step of context.steps ?? []) {
        lines.push(`検索 ${step.round}: ${step.query}`, step.summary);
      }
    }
    const candidateSources = researchSources(context);
    if (candidateSources.length) {
      lines.push("出典:", ...candidateSources.map((source, index) => `[${index + 1}] ${source.title}${safeHttpUrl(source.url) ? ` — ${safeHttpUrl(source.url)}` : ""}`));
    }
  }
  if (sources.length) {
    lines.push("比較回答の出典:", ...sources.map((source, index) => `[${index + 1}] ${source.title}${safeHttpUrl(source.url) ? ` — ${safeHttpUrl(source.url)}` : ""}`));
  }
  return lines;
}
