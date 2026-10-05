import type { AxisKey, CandidateComparison, CandidateReport, ComparisonMethodRun, ComparisonMethods, KnowledgeBaseline, ResearchSource } from "../types";
import { AXIS_ORDER, axisResultFor, axisUnavailableLabel, metricAvailabilityLabel, normalizeWeights } from "./report";
import { isMeasuredCandidate, proposalEvidence, proposalHeading, proposalStrength, candidatePosition, comparisonChange, baselineIsOffline } from "./proposal";
import { cityHighlights } from "./highlights";
import { buildEvidenceSummary, comparisonAxisLabel, type EvidenceSummary } from "./decision";
import { citationHtml, controlledFields, methodLabel, methodSources, methodStatusLabel, researchSources, safeHttpUrl, searchRoundCount } from "./methodComparison";

export interface HtmlComparisonInput {
  savedAt: string;
  preference: string;
  weights: Record<AxisKey, number>;
  candidates: CandidateReport[];
  commander?: CandidateComparison;
  baseline?: KnowledgeBaseline;
  comparisonMethods?: ComparisonMethods;
}

const escapeHtml = (value: unknown): string =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const scoreText = (score: number): string =>
  Number.isInteger(score) ? String(score) : score.toFixed(1);

function modeLabel(candidate: CandidateReport): string {
  if (candidate.source === "web" || candidate.report.plan.data_mode === "web_search" || candidate.report.research_context?.method === "web_search") {
    return candidate.report.research_context?.status === "offline" ? "Web検索未実行" : "Web検索型";
  }
  if (candidate.report.research_context?.method === "data_context") {
    return candidate.report.research_context.status === "verified" ? "データ収集型" : "データ未取得";
  }
  if (candidate.source === "knowledge" || candidate.report.plan.data_mode === "knowledge_only") {
    return candidate.report.execution_steps.some((step) => step.status === "fallback")
      ? "オフライン代替"
      : "LLM知識のみ";
  }
  if (candidate.source === "demo" || candidate.report.plan.data_mode === "mock") return "デモ表示";
  if (candidate.report.plan.data_mode === "open_data") return "公式公開データ";
  return "外部データ";
}

function hasMeasuredData(candidate: CandidateReport): boolean {
  return isMeasuredCandidate(candidate);
}

function comparisonMode(candidates: CandidateReport[]): string {
  const labels = Array.from(new Set(candidates.map(modeLabel)));
  return labels.length === 1 ? labels[0] : "モード混在";
}

function listItems(items: string[], empty = "—"): string {
  if (!items.length) return `<li class="empty">${empty}</li>`;
  return items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function candidateCard(
  candidate: CandidateReport,
  weights: Record<AxisKey, number>,
  recommendedCode: string | null,
): string {
  const candidateCode = candidate.report.plan.region.municipality_code;
  const prioritizedFindings = [...candidate.report.axis_results]
    .sort((left, right) => (weights[right.axis] ?? 0) - (weights[left.axis] ?? 0))
    .slice(0, 2);
  const findings = prioritizedFindings.map((result) => `${result.label}: ${result.narrative.summary}`);
  const cautions = prioritizedFindings.flatMap((result) => result.narrative.cautions.slice(0, 1));
  const rankLabel = candidateCode === recommendedCode ? "司令塔の提案候補" : "比較候補";
  return `
    <article class="rank-card">
      <div class="rank-card-top">
        <span class="rank-number">${escapeHtml(rankLabel)}</span>
        <span class="mode-pill">${escapeHtml(modeLabel(candidate))}</span>
      </div>
      <h3>${escapeHtml(candidate.report.plan.region.name)}</h3>
      <p class="rank-confidence">各専門Agentの根拠確度 ${escapeHtml(Math.round(candidate.report.research_confidence * 100))}%</p>
      <div class="rank-columns">
        <div><span class="label">今回の条件に関係する調査</span><ul>${listItems(findings)}</ul></div>
        <div><span class="label">この候補で確認したい点</span><ul>${listItems(cautions)}</ul></div>
      </div>
    </article>`;
}

function sourceItems(candidates: CandidateReport[]): string {
  const sources = new Map<string, { name: string; date: string; url: string; note: string }>();
  for (const candidate of candidates) {
    for (const result of candidate.report.axis_results) {
      for (const metric of result.metrics) {
        if (metric.quality <= 0) continue;
        sources.set(`${metric.source.source_id}:${metric.source.reference_date}`, {
          name: metric.source.source_name,
          date: metric.source.reference_date,
          url: metric.source.url,
          note: metric.source.commercial_use_note,
        });
      }
    }
  }
  if (!sources.size) return `<li class="empty">出典情報はありません。</li>`;
  return Array.from(sources.values()).map((source) => {
    const label = `${source.name} / ${source.date}`;
    const linked = source.url
      ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(label)} ↗</a>`
      : escapeHtml(label);
    return `<li>${linked}<br><small>${escapeHtml(source.note)}</small></li>`;
  }).join("");
}

function missingMetricItems(candidates: CandidateReport[]): string {
  const items = candidates.flatMap((candidate) => candidate.report.axis_results.flatMap((result) =>
    result.metrics.filter((metric) => metric.quality <= 0).map((metric) =>
      `<li>${escapeHtml(candidate.report.plan.region.name)} / ${escapeHtml(metric.label)}: ${escapeHtml(metricAvailabilityLabel(metric))}</li>`
    ),
  ));
  return items.length ? items.join("") : `<li class="empty">未取得指標はありません。</li>`;
}

function comparisonTable(candidates: CandidateReport[], weights: Record<AxisKey, number>, decision: EvidenceSummary): string {
  const comparisonAxes = AXIS_ORDER.filter((axis) =>
    candidates.some((candidate) => candidate.report.plan.enabled_axes.includes(axis)),
  );
  const normalized = normalizeWeights(weights, decision.sharedAxes);
  const header = candidates.map((candidate) =>
    `<th><span>${escapeHtml(candidate.report.plan.region.name)}</span><small>${escapeHtml(modeLabel(candidate))}</small></th>`,
  ).join("");
  const rows = comparisonAxes.map((axis) => {
    const cells = candidates.map((candidate) => {
      const result = axisResultFor(candidate.report, axis);
      if (!result) return `<td class="missing">${escapeHtml(axisUnavailableLabel(candidate.report, axis))}</td>`;
      if (!decision.sharedAxes.includes(axis)) return `<td class="missing">共通指標が揃っていません</td>`;
      return `<td><div class="table-score">${escapeHtml(scoreText(result.score))}</div><small>視点内の参考値 / 根拠確度 ${Math.round(result.confidence * 100)}%</small></td>`;
    }).join("");
    return `<tr><th class="axis-head"><span>${escapeHtml(comparisonAxisLabel(candidates, axis))}</span><small>${decision.sharedAxes.includes(axis) ? `司令塔へ伝えた優先度 ${Math.round(normalized[axis])}%` : "比較外"}</small></th>${cells}</tr>`;
  }).join("");
  return `<div class="table-scroll"><table><thead><tr><th class="axis-head">評価軸</th>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function methodCardHtml(method: "data_context" | "web_search", run?: ComparisonMethodRun): string {
  const sources = methodSources(run);
  const summarySources = methodSources(run);
  const fields = controlledFields(run);
  const rounds = searchRoundCount(run);
  const parts: string[] = [];
  parts.push(`<article class="method-report-card"><header><div><small>${method === "data_context" ? "方式 1 / データ収集型" : "方式 2 / Web検索型"}</small><h3>${methodLabel(method)}</h3></div><strong>${methodStatusLabel(method, run)}</strong></header>`);
  parts.push(`<p class="method-explainer">${method === "data_context" ? "担当Agentが公式・公開データを集め、指定した条件と確認可能な項目を回答へ反映します。" : "1つのAgentがWeb検索を重ね、見つけた情報を引用付きで回答へまとめます。"}</p>`);
  parts.push("<h4>指定・検証できる前提</h4>" + (fields.length ? "<ul>" + fields.map(item => `<li>${escapeHtml(item)}</li>`).join("") + "</ul>" : "<p>前提の記録はありません。</p>"));
  parts.push("<h4>確認できないこと・制約</h4>" + (run ? limitationsHtml(run) : "<p>この方式の結果を受信していません。</p>"));
  if (method === "web_search") parts.push(`<p class="method-round-count"><strong>${rounds.rounds}</strong> / 最大${rounds.maxRounds || "—"} 回検索</p>`);
  if (method === "web_search" && methodStatusLabel(method, run) === "Web検索未実行") parts.push(`<p class="method-offline-note">Web検索未実行。検索結果や出典を使った回答ではありません。</p>`);
  if (run?.error) parts.push(`<p class="method-error">${escapeHtml(run.error)}</p>`);
  if (run?.status === "failed") parts.push(`<div class="method-incomplete"><strong>この方式は未完了</strong><p>回答を確定できる比較結果がありません。もう一方の方式とは分けて表示しています。</p></div>`);
  if (run?.comparison) {
    const comparison = run.comparison.narrative;
    parts.push(`<h4>比較回答</h4><p class="method-answer">${citationHtml(comparison.summary, summarySources)}</p>`);
    if (comparison.reasons.length) parts.push(`<h4>理由</h4><ul>${comparison.reasons.map(item => `<li>${citationHtml(item, summarySources)}</li>`).join("")}</ul>`);
    if (comparison.tradeoffs.length) parts.push(`<h4>候補間で確認したい違い</h4><ul>${comparison.tradeoffs.map(item => `<li>${citationHtml(item, summarySources)}</li>`).join("")}</ul>`);
    if (comparison.next_checks.length) parts.push(`<h4>次に確認すること</h4><ul>${comparison.next_checks.map(item => `<li>${citationHtml(item, summarySources)}</li>`).join("")}</ul>`);
  } else if (!run) {
    parts.push(`<div class="method-incomplete"><strong>この方式は未完了</strong><p>この方式の結果を受信していません。</p></div>`);
  }
  if (run?.candidates.length) {
    parts.push(`<details class="method-details"><summary>都市ごとの回答・検索記録・出典を見る</summary>`);
    for (const candidate of run.candidates) {
      const report = candidate.report;
      const context = report.research_context;
      const candidateSources = researchSources(context);
      parts.push(`<article class="method-city"><h4>${escapeHtml(report.plan.region.name)}</h4><p>${citationHtml(report.narrative.executive_summary, candidateSources)}</p>`);
      if (report.narrative.strengths.length) parts.push(`<h5>確認した点</h5><ul>${report.narrative.strengths.map(item => `<li>${citationHtml(item, candidateSources)}</li>`).join("")}</ul>`);
      if (report.narrative.cautions.length) parts.push(`<h5>制約・注意点</h5><ul>${report.narrative.cautions.map(item => `<li>${citationHtml(item, candidateSources)}</li>`).join("")}</ul>`);
      if (method === "web_search" && context?.steps?.length) {
        parts.push(`<details class="method-search"><summary>検索した内容（${context.steps.length} 回）</summary><ol>${context.steps.map(step => `<li><strong>第${step.round}回：${escapeHtml(step.query)}</strong><p>${citationHtml(step.summary, context.sources ?? [])}</p>${(step.sources ?? []).map(source => { const url = safeHttpUrl(source.url); return url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title || source.url)} ↗</a>` : ""; }).join(" / ")}</li>`).join("")}</ol></details>`);
      }
      parts.push(sourceLinksHtml(candidateSources));
      parts.push(`</article>`);
    }
    parts.push(`</details>`);
  }
  parts.push(`<details class="method-details"><summary>出典一覧を見る</summary>${sourceLinksHtml(sources)}</details>`);
  parts.push(`</article>`);
  return parts.join("");
}

function limitationsHtml(run: ComparisonMethodRun): string {
  const values = uniqueMethodLimitations(run);
  return values.length ? `<ul>${values.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p>制約の記録はありません。</p>`;
}

function uniqueMethodLimitations(run: ComparisonMethodRun): string[] {
  return [...new Set(run.candidates.flatMap(candidate => candidate.report.research_context?.limitations ?? []))];
}

function sourceLinksHtml(sources: ResearchSource[]): string {
  if (!sources.length) return `<p>登録された出典はありません。</p>`;
  return `<ol class="method-source-list">${sources.map((source, index) => {
    const url = safeHttpUrl(source.url) ?? "";
    return `<li>[${index + 1}] ${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title || source.url)} ↗</a>` : escapeHtml(source.title)}</li>`;
  }).join("")}</ol>`;
}

export function renderComparisonHtml(input: HtmlComparisonInput): string {
  const decision = buildEvidenceSummary(input.candidates, input.weights, input.preference);
  const winner = input.commander?.recommended_region_code
    ? input.candidates.find((candidate) => candidate.report.plan.region.municipality_code === input.commander?.recommended_region_code) ?? null
    : null;
  const ranking = winner
    ? [winner, ...input.candidates.filter((candidate) => candidate !== winner)]
    : input.candidates;
  const featured = winner;
  const highlights = featured ? cityHighlights(featured, input.weights) : [];
  const caution = input.candidates.some((candidate) => !hasMeasuredData(candidate));
  const commuteUnverified = /通勤|勤務地:/.test(input.preference.replaceAll("通勤なし", ""));
  const railUnverified = /新幹線|鉄道|電車|駅近/.test(input.preference);
  const generatedAt = new Date(input.savedAt).toLocaleString("ja-JP", { dateStyle: "long", timeStyle: "short" });
  const pairedComparison = !!input.comparisonMethods;
  const title = pairedComparison ? "都市選びの2方式比較" : proposalHeading(input.candidates, input.commander);
  const lead = pairedComparison
    ? "同じ条件で実行した2つの方法の回答を並べ、前提・根拠・出典を確認できます。"
    : input.commander?.narrative.summary ?? "専門Agentの調査結果を司令塔がまとめます。軸別の数値だけでは候補を選びません。";
  const cautionText = input.candidates.length < 2 ? "軸ごとの優劣を比べるには候補が2件以上必要です。"
    : !input.commander ? "司令塔の統合結果がありません。候補の順位は表示していません。"
    : !winner ? "この条件で候補を一つに絞るだけの比較根拠がありません。"
    : caution ? "デモ値または外部データ未参照の結果です。実在地域を選ぶ根拠には使用できません。"
      : commuteUnverified ? "実際の通勤時間・経路は未測定です。この選択が通勤の優劣を証明するものではありません。"
        : railUnverified ? "指定した鉄道・新幹線への実際のアクセスは未測定です。"
      : "司令塔は暮らしの条件、専門Agentの所見、比較できる根拠をまとめます。軸別の数値は提案の補足です。";
  const candidatesLabel = input.candidates.map((candidate) => candidate.report.plan.region.name).join(" / ");
  const condition = input.preference.trim() || "指定なし";

  const evidence = proposalEvidence(input.candidates, input.weights, input.commander).slice(0, 6);
  const methodSection = input.comparisonMethods
    ? `<section class="section method-comparison-report"><div class="section-head"><div><h2>回答の前提をどこまで指定・検証できるか</h2><p>同じ希望・都市・評価視点で実行した2つの方法を並べました。方法間の優劣は自動で決めていません。</p></div></div><div class="method-report-grid">${methodCardHtml("data_context", input.comparisonMethods.data_context)}${methodCardHtml("web_search", input.comparisonMethods.web_search)}</div></section>`
    : "";
  return `<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}｜${pairedComparison ? "都市選びの比較レポート" : "都市選びの提案レポート"}</title>
  <style>
    :root{--ink:#182233;--muted:#657085;--line:#dbe2ec;--bg:#f4f7fb;--card:#fff;--blue:#4169e1;--blue-deep:#2947a7;--mint:#12a98b;--yellow:#f2b84b;--coral:#eb6b58;--pink:#f28bb8;--lavender:#9a8ce8;--shadow:0 20px 50px rgba(38,56,91,.1)}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;line-height:1.65}a{color:var(--blue-deep)}.shell{width:min(1120px,calc(100% - 36px));margin:0 auto;padding:28px 0 64px}.hero{position:relative;overflow:hidden;padding:42px 46px;background:linear-gradient(132deg,#263e9c 0%,#5577eb 49%,#7a5bc7 100%);border-radius:30px;color:#fff;box-shadow:var(--shadow)}.hero:after{content:"";position:absolute;right:-90px;top:-130px;width:360px;height:360px;border:60px solid rgba(255,255,255,.12);border-radius:50%}.hero-top{display:flex;justify-content:space-between;gap:16px;position:relative;z-index:1;font-size:12px;letter-spacing:.12em;text-transform:uppercase}.hero-top .mode-pill{background:rgba(255,255,255,.18);color:#fff;border-color:rgba(255,255,255,.3)}.hero-layout{display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:28px;align-items:end;position:relative;z-index:1}.kicker{margin:38px 0 4px;font-size:12px;letter-spacing:.16em;opacity:.78}.hero h1{max-width:720px;margin:0;font-size:clamp(2.2rem,6vw,4.8rem);line-height:1.08;letter-spacing:-.06em}.hero-lead{max-width:700px;margin:20px 0 0;font-size:clamp(1.05rem,2vw,1.38rem);font-weight:650}.hero-note{max-width:700px;margin:10px 0 0;font-size:.88rem;opacity:.8}.hero-score-card{padding:22px 24px;border:1px solid rgba(255,255,255,.3);border-radius:22px;background:rgba(255,255,255,.13);backdrop-filter:blur(8px)}.hero-score{font-size:4.5rem;font-weight:800;line-height:1;letter-spacing:-.08em}.hero-score small{font-size:1rem;letter-spacing:0;opacity:.75}.hero-score-card p{margin:10px 0 0;font-size:.76rem;opacity:.82}.mode-pill{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:4px 9px;color:var(--muted);font-size:11px;line-height:1.2;white-space:nowrap}.decision-grid{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:16px;margin:20px 0 44px}.decision-card{min-height:156px;padding:22px 24px;background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 8px 25px rgba(38,56,91,.05)}.decision-card.primary{background:#edf1ff;border-color:#bdc8ff}.decision-card.warn{background:#fff8e9;border-color:#f2d38b}.decision-card h2{margin:0 0 9px;font-size:1rem}.decision-card p{margin:0;color:var(--muted);font-size:.9rem}.decision-card ul,.rank-card ul,.detail-list{margin:8px 0 0;padding:0;list-style:none}.decision-card li,.rank-card li,.detail-list li{margin:5px 0;font-size:.85rem}.decision-card li:before,.rank-card li:before,.detail-list li:before{content:"✓";display:inline-block;width:20px;color:var(--mint);font-weight:800}.decision-card.warn li:before{content:"!";color:var(--coral)}.section{margin-top:44px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:18px;margin-bottom:16px}.section-head h2{margin:0;font-size:1.55rem;letter-spacing:-.04em}.section-head p{margin:0;color:var(--muted);font-size:.85rem}.rank-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.rank-card{padding:22px;border:1px solid var(--line);border-top:6px solid var(--lavender);border-radius:18px;background:var(--card);box-shadow:0 8px 25px rgba(38,56,91,.05)}.rank-card.rank-1{border-top-color:var(--yellow);background:linear-gradient(180deg,#fffdf4,#fff)}.rank-card.rank-2{border-top-color:var(--blue)}.rank-card.rank-3{border-top-color:var(--pink)}.rank-card-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.rank-number{color:var(--blue-deep);font-size:12px;font-weight:800;letter-spacing:.1em}.rank-card h3{margin:20px 0 2px;font-size:1.38rem;letter-spacing:-.05em}.rank-score{font-size:3.2rem;font-weight:800;line-height:1;letter-spacing:-.08em}.rank-score small{margin-left:3px;font-size:.9rem;letter-spacing:0;color:var(--muted)}.rank-confidence{margin:8px 0 18px;color:var(--muted);font-size:.75rem}.rank-columns{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:14px;border-top:1px solid var(--line)}.rank-columns .label,.label{color:var(--muted);font-size:.69rem;font-weight:750;letter-spacing:.06em}.rank-columns li{font-size:.75rem;line-height:1.45}.rank-columns li:before{width:15px}.score-high{color:var(--mint)}.score-mid{color:#c58a1c}.score-low{color:var(--coral)}.table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:18px;background:var(--card);box-shadow:0 8px 25px rgba(38,56,91,.05)}table{width:100%;min-width:650px;border-collapse:collapse}th,td{padding:16px 17px;border-bottom:1px solid var(--line);text-align:left}tr:last-child th,tr:last-child td{border-bottom:0}thead th{background:#f0f3fa;color:var(--muted);font-size:.75rem}thead th:not(.axis-head){color:var(--ink);font-size:.95rem}th span,th small{display:block}th small,td small{color:var(--muted);font-size:.7rem;font-weight:400}.axis-head{width:190px}.axis-head span{font-weight:700}.table-score{font-size:1.45rem;font-weight:800;line-height:1}.bar{height:7px;margin:8px 0 5px;border-radius:99px;background:#e7ebf3}.bar span{display:block;height:100%;border-radius:99px}.bar-high{background:var(--mint)}.bar-mid{background:var(--yellow)}.bar-low{background:var(--coral)}td.missing{color:#9ba5b5}.details-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.detail-card{padding:23px;border:1px solid var(--line);border-radius:18px;background:var(--card)}.detail-card h3{margin:0 0 8px;font-size:1.15rem}.detail-card .summary{margin:0 0 16px;color:var(--muted);font-size:.88rem}.detail-columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.footer-card{margin-top:44px;padding:22px 24px;border-radius:18px;background:#202b42;color:#dfe7ff}.footer-card h2{margin:0 0 8px;font-size:1rem}.footer-card p,.footer-card li{font-size:.82rem}.footer-card ul{margin:8px 0 0;padding-left:20px}.footer-card a{color:#b9c8ff}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:25px;color:var(--muted);font-size:.76rem}.empty{color:var(--muted)!important}.empty:before{content:""!important}@media(max-width:760px){.shell{width:min(100% - 24px,1120px);padding-top:12px}.hero{padding:28px 24px;border-radius:22px}.hero-layout{grid-template-columns:1fr}.hero-score-card{width:fit-content}.decision-grid{grid-template-columns:1fr;margin-bottom:34px}.section-head{display:block}.section-head p{margin-top:5px}.rank-columns,.detail-columns{grid-template-columns:1fr}.hero h1{font-size:2.45rem}}@media print{body{background:#fff}.shell{width:100%;padding:0}.hero{box-shadow:none;break-inside:avoid}.decision-card,.rank-card,.table-scroll,.detail-card{box-shadow:none;break-inside:avoid}.section{break-inside:avoid}.footer-card{color:#182233;background:#f1f4fa;border:1px solid var(--line)}}
    :root{--bg:#f3eee3;--ink:#2e352a;--muted:#667166;--line:#d7d3c5;--blue-deep:#315d57;--mint:#3e7460;--shadow:0 20px 45px rgba(43,52,38,.09)}
    .hero{background:linear-gradient(120deg,#294e45,#407b67 65%,#b78b52);border-radius:12px;min-height:330px}.hero:after{border-color:rgba(255,255,255,.09)}.hero-layout{display:block}.hero h1{max-width:900px}.hero-lead{max-width:820px;font-weight:500}.hero-note{font-size:.78rem}.kicker{letter-spacing:.24em}.section-head h2{font-size:clamp(1.5rem,3vw,2.3rem)}.portrait-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:16px}.portrait-card{padding:25px 26px;background:#fffdf7;border:1px solid var(--line);border-top:5px solid #c99e62;border-radius:8px}.portrait-card .number{display:block;color:#ad814a;font:700 .75rem ui-monospace,monospace;letter-spacing:.12em}.portrait-card h3{margin:12px 0;font-size:1.25rem}.portrait-card p{margin:0;font-size:.94rem}.portrait-card small{display:block;margin-top:15px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted)}.portrait-note{margin-top:15px;color:var(--muted);font-size:.83rem}.decision-card{border-radius:8px}.decision-card.primary{background:#e8f0e9;border-color:#bbd2c0}.decision-card.warn{background:#f7f0e6;border-color:#e8d4b5}.rank-card,.detail-card,.table-scroll{border-radius:8px}.mode-pill{border-radius:3px}.footer-card{border-radius:8px;background:#263b34}
    .method-report-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.method-report-card{min-width:0;padding:20px;border:1px solid var(--line);background:var(--card);overflow-wrap:anywhere}.method-report-card header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;border-bottom:1px solid var(--line);padding-bottom:12px}.method-report-card header h3{margin:4px 0 0}.method-report-card header strong{font-size:.78rem}.method-report-card h4{margin:17px 0 6px;font-size:.9rem}.method-report-card h5{margin:14px 0 4px}.method-report-card p,.method-report-card li{font-size:.83rem}.method-report-card ul{padding-left:20px}.method-report-card li{margin:7px 0}.method-report-card details{margin-top:15px;padding-top:10px;border-top:1px solid var(--line)}.method-report-card summary{cursor:pointer}.method-city{margin:12px 0;padding:14px;background:var(--bg);border:1px solid var(--line)}.method-round-count strong{font-size:1.5rem;color:var(--blue-deep)}.method-offline-note,.method-incomplete,.method-error{padding:10px 12px;background:#f7f0e6;border-left:3px solid #b98539}.method-error{border-color:#a04531;overflow-wrap:anywhere}.method-source-list{padding-left:20px}.method-source-list li{overflow-wrap:anywhere}.method-report-card .inline-citation{font-size:.76em;font-weight:700}.method-report-card a{overflow-wrap:anywhere}
    @media(max-width:760px){.method-report-grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div class="hero-top"><span>Livability / Your City Proposal</span><span class="mode-pill">${pairedComparison ? "2方式を比較" : escapeHtml(comparisonMode(input.candidates))}</span></div>
      <div class="hero-layout">
        <div>
          <p class="kicker">${pairedComparison ? "同じ条件で実行した2方式の回答" : escapeHtml(proposalStrength(input.candidates, input.commander))}</p>
          <h1>${escapeHtml(title)}</h1>
          <p class="hero-lead">${escapeHtml(lead)}</p>
          <p class="hero-note">今回の暮らしの条件: ${escapeHtml(condition)} ／ ${pairedComparison ? "2方式を比較" : escapeHtml(comparisonMode(input.candidates))}</p>
        </div>
      </div>
    </header>

    ${methodSection}
    ${pairedComparison ? `<details class="section legacy-report-details"><summary>都市ごとの補足情報・軸別指標を見る</summary>` : ""}
    <section class="decision-grid" aria-label="結論の要点">
      <article class="decision-card primary"><h2>この条件での提案</h2><p>${escapeHtml(lead)}</p><ul>${listItems(input.commander?.narrative.reasons ?? [], "司令塔の提案は未取得です。")}</ul></article>
      <article class="decision-card"><h2>選ぶ前に知っておきたい違い</h2><ul>${listItems(input.commander?.narrative.tradeoffs ?? [], "比較できるトレードオフはありません。")}</ul></article>
      <article class="decision-card warn"><h2>次に確認すること</h2><ul>${listItems(input.commander?.narrative.next_checks ?? [decision.nextCheck])}</ul></article>
    </section>

    <section class="section"><div class="section-head"><div><h2>暮らしに関わる、確かめられた違い</h2><p>街全体の数字を、あなたの住まい選びの手掛かりに。</p></div></div><div class="portrait-grid">${evidence.length ? evidence.map(item => `<article class="portrait-card"><span class="number">${item.decisive ? "おすすめの理由に使った根拠" : "街を選ぶ手掛かり"}</span><h3>${escapeHtml(item.label)}</h3><p><strong>${escapeHtml(item.verdict)}</strong></p><p>${escapeHtml(item.meaning)}</p><p><strong>物件選びでは</strong><br>${escapeHtml(item.nextCheck)}</p><details><summary>数字・出典を見る</summary><p>${escapeHtml(item.metricLabel)}<br>${escapeHtml(item.comparison)}</p><small>${escapeHtml(item.difference)}<br>${escapeHtml(item.source)} ${item.urls.map(url => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">出典 ↗</a>`).join(" / ")}</small></details></article>`).join("") : `<p>比較可能な出典付きの数値根拠はありません。提案は調査前の仮説です。</p>`}</div></section>
    <section class="section"><div class="section-head"><div><h2>各都市を選ぶ条件</h2></div></div><div class="portrait-grid">${input.candidates.map(candidate => { const position = candidatePosition(input, candidate); return `<article class="portrait-card"><h3>${escapeHtml(candidate.report.plan.region.name)}</h3><p>${escapeHtml(position.fit)}</p><small>選ぶ条件：${escapeHtml(position.condition)}</small></article>`; }).join("")}</div></section>
    ${input.baseline ? `<section class="section"><div class="section-head"><div><h2>街の印象から、確かめた提案へ</h2><p>${escapeHtml(comparisonChange(input))}</p></div></div><div class="portrait-grid"><article class="portrait-card"><h3>${baselineIsOffline(input.baseline) ? "調べる前（代替処理を含む）" : "調べる前（AIの一般知識）"}</h3><p>${escapeHtml(input.baseline.commander.narrative.summary)}</p><ul>${listItems(input.baseline.commander.narrative.reasons)}</ul><small>次の確認</small><ul>${listItems(input.baseline.commander.narrative.next_checks)}</ul></article><article class="portrait-card"><h3>確かめた後（地域データを使用）</h3><p>${escapeHtml(lead)}</p><ul>${listItems(input.commander?.narrative.reasons ?? [])}</ul><strong>数字で確かめられたこと</strong><ul>${listItems(evidence.slice(0, 3).map(item => item.verdict))}</ul><small>次の確認</small><ul>${listItems(input.commander?.narrative.next_checks ?? [])}</ul></article></div><p class="portrait-note">同じ都市・希望・優先度とコマンダーで比較。調査段階の構成は異なるため厳密なデータ効果の実験ではありません。</p></section>` : ""}
    <section class="section"><div class="section-head"><div><h2>候補ごとの調査メモ</h2><p>各専門Agentが条件に関係する所見と確認事項を整理しました。</p></div><span class="mode-pill">${escapeHtml(condition)}</span></div><div class="rank-grid">${ranking.map((candidate) => candidateCard(candidate, input.weights, input.commander?.recommended_region_code ?? null)).join("")}</div></section>

    <details class="section evidence-disclosure"><summary>5つの視点の参考スコアを見る（候補を選ぶ合計点ではありません）</summary><div class="section-head"><div><h2>司令塔が参照した根拠</h2><p>軸内の数値は補足です。候補間の提案理由は上の定性的な統合結果を確認してください。</p></div></div>${comparisonTable(ranking, input.weights, decision)}<p class="portrait-note">${escapeHtml(cautionText)}</p></details>

    <section class="section" aria-label="街の魅力"><div class="section-head"><div><h2>${featured ? escapeHtml(featured.report.plan.region.name) : "この街"}の魅力</h2><p>専門Agentの所見と今回の条件から、暮らしの要点を紹介します。</p></div><p>生成: ${escapeHtml(generatedAt)}</p></div><div class="portrait-grid">${highlights.length ? highlights.map((highlight, index) => `<article class="portrait-card"><span class="number">STORY 0${index + 1}</span><h3>${escapeHtml(highlight.label)}</h3><p>${escapeHtml(highlight.summary)}</p><small>${highlight.evidence ? `${highlight.contextOnly ? "街の背景情報・採点対象外" : "参考指標"} ／ ${escapeHtml(highlight.evidence)} ／ ${escapeHtml(highlight.source)}` : "評価結果に基づく見立て。数値根拠は未取得"}</small></article>`).join("") : `<article class="portrait-card"><h3>確認できる強みなし</h3><p>今回のデータでは確かな強みを断定できません。専門Agentの根拠をご確認ください。</p></article>`}</div></section>

    <section class="footer-card"><h2>根拠・データの扱い</h2><p>${escapeHtml(cautionText)} 専門Agentが取得した根拠を司令塔が暮らしの条件に照らしてまとめます。未取得の値は0として採点せず、提案の限界と確認事項に反映します。</p><ul>${sourceItems(input.candidates)}</ul><h3>未取得指標の状態</h3><ul>${missingMetricItems(input.candidates)}</ul><p class="meta">対象: ${escapeHtml(candidatesLabel)} ／ 条件: ${escapeHtml(condition)} ／ 保存日時: ${escapeHtml(generatedAt)}</p></section>
    ${pairedComparison ? `</details>` : ""}
  </main>
</body>
</html>`;
}

export { escapeHtml };
