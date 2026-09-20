import type { AxisKey, CandidateReport } from "../types";
import { AXIS_LABELS, AXIS_ORDER, axisResultFor, normalizeWeights, weightedScore } from "./report";

export interface HtmlComparisonInput {
  savedAt: string;
  preference: string;
  weights: Record<AxisKey, number>;
  candidates: CandidateReport[];
}

const escapeHtml = (value: unknown): string =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const scoreValue = (score: number): number => Math.max(0, Math.min(100, score));
const scoreText = (score: number): string =>
  Number.isInteger(score) ? String(score) : score.toFixed(1);
const scoreClass = (score: number): "high" | "mid" | "low" =>
  score >= 70 ? "high" : score >= 50 ? "mid" : "low";

function modeLabel(candidate: CandidateReport): string {
  if (candidate.source === "knowledge" || candidate.report.plan.data_mode === "knowledge_only") {
    return candidate.report.execution_steps.some((step) => step.status === "fallback")
      ? "オフライン代替"
      : "LLM知識のみ";
  }
  if (candidate.source === "demo" || candidate.report.plan.data_mode === "mock") return "デモ表示";
  return "外部データ";
}

function comparisonMode(candidates: CandidateReport[]): string {
  const labels = Array.from(new Set(candidates.map(modeLabel)));
  return labels.length === 1 ? labels[0] : "モード混在";
}

function rankedCandidates(
  candidates: CandidateReport[],
  weights: Record<AxisKey, number>,
): CandidateReport[] {
  return candidates
    .map((candidate, index) => ({ candidate, index, score: weightedScore(candidate.report, weights) }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(({ candidate }) => candidate);
}

function axesByScore(candidate: CandidateReport, descending: boolean) {
  return AXIS_ORDER
    .map((axis) => ({ axis, result: axisResultFor(candidate.report, axis) }))
    .filter((item): item is { axis: AxisKey; result: NonNullable<typeof item.result> } => Boolean(item.result))
    .sort((a, b) => (descending ? b.result.score - a.result.score : a.result.score - b.result.score));
}

function listItems(items: string[], empty = "—"): string {
  if (!items.length) return `<li class="empty">${empty}</li>`;
  return items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function candidateCard(
  candidate: CandidateReport,
  rank: number,
  weights: Record<AxisKey, number>,
): string {
  const score = weightedScore(candidate.report, weights);
  const tone = scoreClass(score);
  const best = axesByScore(candidate, true).slice(0, 2);
  const watch = axesByScore(candidate, false).slice(0, 2);
  const rankLabel = rank === 1 ? "第一候補" : rank === 2 ? "次点" : `${rank}位`;
  return `
    <article class="rank-card rank-${rank}">
      <div class="rank-card-top">
        <span class="rank-number">${escapeHtml(rankLabel)}</span>
        <span class="mode-pill">${escapeHtml(modeLabel(candidate))}</span>
      </div>
      <h3>${escapeHtml(candidate.report.plan.region.name)}</h3>
      <div class="rank-score score-${tone}">${escapeHtml(scoreText(score))}<small>/100</small></div>
      <p class="rank-confidence">総合信頼度 ${escapeHtml(Math.round(candidate.report.overall_confidence * 100))}%</p>
      <div class="rank-columns">
        <div><span class="label">伸びている軸</span><ul>${listItems(best.map(({ axis, result }) => `${AXIS_LABELS[axis]} ${scoreText(result.score)}点`))}</ul></div>
        <div><span class="label">先に確認する軸</span><ul>${listItems(watch.map(({ axis, result }) => `${AXIS_LABELS[axis]} ${scoreText(result.score)}点`))}</ul></div>
      </div>
    </article>`;
}

function sourceItems(candidates: CandidateReport[]): string {
  const sources = new Map<string, { name: string; date: string; url: string }>();
  for (const candidate of candidates) {
    for (const result of candidate.report.axis_results) {
      for (const metric of result.metrics) {
        sources.set(metric.source.source_id, {
          name: metric.source.source_name,
          date: metric.source.reference_date,
          url: metric.source.url,
        });
      }
    }
  }
  if (!sources.size) return `<li class="empty">出典情報はありません。</li>`;
  return Array.from(sources.values()).map((source) => {
    const label = `${source.name} / ${source.date}`;
    return source.url
      ? `<li><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(label)} ↗</a></li>`
      : `<li>${escapeHtml(label)}</li>`;
  }).join("");
}

function comparisonTable(candidates: CandidateReport[], weights: Record<AxisKey, number>): string {
  const normalized = normalizeWeights(weights);
  const header = candidates.map((candidate) =>
    `<th><span>${escapeHtml(candidate.report.plan.region.name)}</span><small>${escapeHtml(modeLabel(candidate))}</small></th>`,
  ).join("");
  const rows = AXIS_ORDER.map((axis) => {
    const cells = candidates.map((candidate) => {
      const result = axisResultFor(candidate.report, axis);
      if (!result) return `<td class="missing">—</td>`;
      const value = scoreValue(result.score);
      return `<td><div class="table-score score-${scoreClass(result.score)}">${escapeHtml(scoreText(result.score))}</div><div class="bar"><span class="bar-${scoreClass(result.score)}" style="width:${value}%"></span></div><small>信頼度 ${Math.round(result.confidence * 100)}%</small></td>`;
    }).join("");
    return `<tr><th class="axis-head"><span>${escapeHtml(AXIS_LABELS[axis])}</span><small>重み ${Math.round(normalized[axis])}%</small></th>${cells}</tr>`;
  }).join("");
  return `<div class="table-scroll"><table><thead><tr><th class="axis-head">評価軸</th>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function renderComparisonHtml(input: HtmlComparisonInput): string {
  const ranking = rankedCandidates(input.candidates, input.weights);
  const winner = ranking[0];
  const runnerUp = ranking[1];
  const winnerScore = winner ? weightedScore(winner.report, input.weights) : 0;
  const runnerUpScore = runnerUp ? weightedScore(runnerUp.report, input.weights) : 0;
  const gap = winner && runnerUp ? Math.round((winnerScore - runnerUpScore) * 10) / 10 : 0;
  const winnerBest = winner ? axesByScore(winner, true).slice(0, 3) : [];
  const winnerWatch = winner ? axesByScore(winner, false).slice(0, 3) : [];
  const caution = input.candidates.some((candidate) => modeLabel(candidate) !== "外部データ");
  const generatedAt = new Date(input.savedAt).toLocaleString("ja-JP", { dateStyle: "long", timeStyle: "short" });
  const title = winner ? `${winner.report.plan.region.name}を第一候補にします` : "候補地を比較します";
  const lead = winner
    ? `${winner.report.plan.region.name}を第一候補にします。${gap ? `次点との差は${scoreText(gap)}点です。` : "候補間の差はありません。"}`
    : "候補地を入力して比較を実行してください。";
  const cautionText = caution
    ? "この比較には外部データ未参照またはデモ値が含まれます。方向性を決めるために使い、契約・引っ越し前に一次情報を確認してください。"
    : "この結論は入力した条件と表示上の重みに基づく比較です。契約・引っ越し前は物件と現地条件を確認してください。";
  const candidatesLabel = input.candidates.map((candidate) => candidate.report.plan.region.name).join(" / ");
  const condition = input.preference.trim() || "指定なし";

  return `<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}｜住みやすさ比較レポート</title>
  <style>
    :root{--ink:#182233;--muted:#657085;--line:#dbe2ec;--bg:#f4f7fb;--card:#fff;--blue:#4169e1;--blue-deep:#2947a7;--mint:#12a98b;--yellow:#f2b84b;--coral:#eb6b58;--pink:#f28bb8;--lavender:#9a8ce8;--shadow:0 20px 50px rgba(38,56,91,.1)}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;line-height:1.65}a{color:var(--blue-deep)}.shell{width:min(1120px,calc(100% - 36px));margin:0 auto;padding:28px 0 64px}.hero{position:relative;overflow:hidden;padding:42px 46px;background:linear-gradient(132deg,#263e9c 0%,#5577eb 49%,#7a5bc7 100%);border-radius:30px;color:#fff;box-shadow:var(--shadow)}.hero:after{content:"";position:absolute;right:-90px;top:-130px;width:360px;height:360px;border:60px solid rgba(255,255,255,.12);border-radius:50%}.hero-top{display:flex;justify-content:space-between;gap:16px;position:relative;z-index:1;font-size:12px;letter-spacing:.12em;text-transform:uppercase}.hero-top .mode-pill{background:rgba(255,255,255,.18);color:#fff;border-color:rgba(255,255,255,.3)}.hero-layout{display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:28px;align-items:end;position:relative;z-index:1}.kicker{margin:38px 0 4px;font-size:12px;letter-spacing:.16em;opacity:.78}.hero h1{max-width:720px;margin:0;font-size:clamp(2.2rem,6vw,4.8rem);line-height:1.08;letter-spacing:-.06em}.hero-lead{max-width:700px;margin:20px 0 0;font-size:clamp(1.05rem,2vw,1.38rem);font-weight:650}.hero-note{max-width:700px;margin:10px 0 0;font-size:.88rem;opacity:.8}.hero-score-card{padding:22px 24px;border:1px solid rgba(255,255,255,.3);border-radius:22px;background:rgba(255,255,255,.13);backdrop-filter:blur(8px)}.hero-score{font-size:4.5rem;font-weight:800;line-height:1;letter-spacing:-.08em}.hero-score small{font-size:1rem;letter-spacing:0;opacity:.75}.hero-score-card p{margin:10px 0 0;font-size:.76rem;opacity:.82}.mode-pill{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:4px 9px;color:var(--muted);font-size:11px;line-height:1.2;white-space:nowrap}.decision-grid{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:16px;margin:20px 0 44px}.decision-card{min-height:156px;padding:22px 24px;background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 8px 25px rgba(38,56,91,.05)}.decision-card.primary{background:#edf1ff;border-color:#bdc8ff}.decision-card.warn{background:#fff8e9;border-color:#f2d38b}.decision-card h2{margin:0 0 9px;font-size:1rem}.decision-card p{margin:0;color:var(--muted);font-size:.9rem}.decision-card ul,.rank-card ul,.detail-list{margin:8px 0 0;padding:0;list-style:none}.decision-card li,.rank-card li,.detail-list li{margin:5px 0;font-size:.85rem}.decision-card li:before,.rank-card li:before,.detail-list li:before{content:"✓";display:inline-block;width:20px;color:var(--mint);font-weight:800}.decision-card.warn li:before{content:"!";color:var(--coral)}.section{margin-top:44px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:18px;margin-bottom:16px}.section-head h2{margin:0;font-size:1.55rem;letter-spacing:-.04em}.section-head p{margin:0;color:var(--muted);font-size:.85rem}.rank-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.rank-card{padding:22px;border:1px solid var(--line);border-top:6px solid var(--lavender);border-radius:18px;background:var(--card);box-shadow:0 8px 25px rgba(38,56,91,.05)}.rank-card.rank-1{border-top-color:var(--yellow);background:linear-gradient(180deg,#fffdf4,#fff)}.rank-card.rank-2{border-top-color:var(--blue)}.rank-card.rank-3{border-top-color:var(--pink)}.rank-card-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.rank-number{color:var(--blue-deep);font-size:12px;font-weight:800;letter-spacing:.1em}.rank-card h3{margin:20px 0 2px;font-size:1.38rem;letter-spacing:-.05em}.rank-score{font-size:3.2rem;font-weight:800;line-height:1;letter-spacing:-.08em}.rank-score small{margin-left:3px;font-size:.9rem;letter-spacing:0;color:var(--muted)}.rank-confidence{margin:8px 0 18px;color:var(--muted);font-size:.75rem}.rank-columns{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:14px;border-top:1px solid var(--line)}.rank-columns .label,.label{color:var(--muted);font-size:.69rem;font-weight:750;letter-spacing:.06em}.rank-columns li{font-size:.75rem;line-height:1.45}.rank-columns li:before{width:15px}.score-high{color:var(--mint)}.score-mid{color:#c58a1c}.score-low{color:var(--coral)}.table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:18px;background:var(--card);box-shadow:0 8px 25px rgba(38,56,91,.05)}table{width:100%;min-width:650px;border-collapse:collapse}th,td{padding:16px 17px;border-bottom:1px solid var(--line);text-align:left}tr:last-child th,tr:last-child td{border-bottom:0}thead th{background:#f0f3fa;color:var(--muted);font-size:.75rem}thead th:not(.axis-head){color:var(--ink);font-size:.95rem}th span,th small{display:block}th small,td small{color:var(--muted);font-size:.7rem;font-weight:400}.axis-head{width:190px}.axis-head span{font-weight:700}.table-score{font-size:1.45rem;font-weight:800;line-height:1}.bar{height:7px;margin:8px 0 5px;border-radius:99px;background:#e7ebf3}.bar span{display:block;height:100%;border-radius:99px}.bar-high{background:var(--mint)}.bar-mid{background:var(--yellow)}.bar-low{background:var(--coral)}td.missing{color:#9ba5b5}.details-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.detail-card{padding:23px;border:1px solid var(--line);border-radius:18px;background:var(--card)}.detail-card h3{margin:0 0 8px;font-size:1.15rem}.detail-card .summary{margin:0 0 16px;color:var(--muted);font-size:.88rem}.detail-columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.footer-card{margin-top:44px;padding:22px 24px;border-radius:18px;background:#202b42;color:#dfe7ff}.footer-card h2{margin:0 0 8px;font-size:1rem}.footer-card p,.footer-card li{font-size:.82rem}.footer-card ul{margin:8px 0 0;padding-left:20px}.footer-card a{color:#b9c8ff}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:25px;color:var(--muted);font-size:.76rem}.empty{color:var(--muted)!important}.empty:before{content:""!important}@media(max-width:760px){.shell{width:min(100% - 24px,1120px);padding-top:12px}.hero{padding:28px 24px;border-radius:22px}.hero-layout{grid-template-columns:1fr}.hero-score-card{width:fit-content}.decision-grid{grid-template-columns:1fr;margin-bottom:34px}.section-head{display:block}.section-head p{margin-top:5px}.rank-columns,.detail-columns{grid-template-columns:1fr}.hero h1{font-size:2.45rem}}@media print{body{background:#fff}.shell{width:100%;padding:0}.hero{box-shadow:none;break-inside:avoid}.decision-card,.rank-card,.table-scroll,.detail-card{box-shadow:none;break-inside:avoid}.section{break-inside:avoid}.footer-card{color:#182233;background:#f1f4fa;border:1px solid var(--line)}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div class="hero-top"><span>Livability / Decision Brief</span><span class="mode-pill">${escapeHtml(comparisonMode(input.candidates))}</span></div>
      <div class="hero-layout">
        <div>
          <p class="kicker">あなたの条件なら</p>
          <h1>${escapeHtml(title)}</h1>
          <p class="hero-lead">${escapeHtml(lead)}</p>
          <p class="hero-note">総合点は、表示上の重みで5軸を再計算した比較スコアです。${escapeHtml(cautionText)}</p>
        </div>
        <div class="hero-score-card"><div class="hero-score">${escapeHtml(scoreText(winnerScore))}<small>/100</small></div><p>第一候補の表示上の総合点</p></div>
      </div>
    </header>

    <section class="decision-grid" aria-label="結論の要点">
      <article class="decision-card primary"><h2>なぜこの候補か</h2><ul>${listItems(winnerBest.map(({ axis, result }) => `${AXIS_LABELS[axis]}が${scoreText(result.score)}点。${result.narrative.summary}`), "候補を比較してください。")}</ul></article>
      <article class="decision-card"><h2>判断の分かれ目</h2><p>${runnerUp ? `次点は${escapeHtml(runnerUp.report.plan.region.name)}（${escapeHtml(scoreText(runnerUpScore))}点）。${gap ? `差は${escapeHtml(scoreText(gap))}点です。` : "差がないため、弱点の少なさで選びます。"}` : "候補を2つ以上入れると差が表示されます。"}</p></article>
      <article class="decision-card warn"><h2>決める前に見ること</h2><ul>${listItems(winnerWatch.map(({ axis, result }) => `${AXIS_LABELS[axis]}は${scoreText(result.score)}点。${result.narrative.cautions[0] ?? "現地条件を確認"}`), "一次情報と現地条件を確認してください。")}</ul></article>
    </section>

    <section class="section"><div class="section-head"><div><h2>候補ランキング</h2><p>1位を第一候補として、弱点を確認してから決めるための並びです。</p></div><span class="mode-pill">${escapeHtml(condition)}</span></div><div class="rank-grid">${ranking.map((candidate, index) => candidateCard(candidate, index + 1, input.weights)).join("")}</div></section>

    <section class="section"><div class="section-head"><div><h2>5軸で比較</h2><p>バーが長いほど、今回の条件に対する適合度が高い見立てです。</p></div><p>生成: ${escapeHtml(generatedAt)}</p></div>${comparisonTable(ranking, input.weights)}</section>

    <section class="section"><div class="section-head"><div><h2>候補別の要点</h2><p>総合点だけでなく、強みと注意点を読んで意思決定します。</p></div></div><div class="details-grid">${ranking.map((candidate) => `<article class="detail-card"><h3>${escapeHtml(candidate.report.plan.region.name)} <span class="mode-pill">${escapeHtml(modeLabel(candidate))}</span></h3><p class="summary">${escapeHtml(candidate.report.narrative.executive_summary)}</p><div class="detail-columns"><div><span class="label">強み</span><ul class="detail-list">${listItems(candidate.report.narrative.strengths)}</ul></div><div><span class="label">注意点</span><ul class="detail-list">${listItems(candidate.report.narrative.cautions)}</ul></div></div></article>`).join("")}</div></section>

    <section class="footer-card"><h2>根拠・データの扱い</h2><p>${escapeHtml(cautionText)}</p><ul>${sourceItems(input.candidates)}</ul><p class="meta">対象: ${escapeHtml(candidatesLabel)} ／ 条件: ${escapeHtml(condition)} ／ 保存日時: ${escapeHtml(generatedAt)}</p></section>
  </main>
</body>
</html>`;
}

export { escapeHtml };
