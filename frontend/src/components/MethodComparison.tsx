import type { ComparisonMethodKey } from "../lib/methodComparison";
import { AXIS_SHORT_LABELS } from "../lib/report";
import {
  controlledFields,
  methodSources,
  methodStatusLabel,
  researchContextOf,
  researchSources,
  safeHttpUrl,
  searchRoundCount,
  limitations,
} from "../lib/methodComparison";
import type { AxisKey, ComparisonMethodRun, ComparisonMethods, ResearchSource } from "../types";

function InlineCitations({ text, sources }: { text: string; sources: ResearchSource[] }) {
  return <>{text.split(/(\[\d+\])/g).map((part, index) => {
    const match = /^\[(\d+)\]$/.exec(part);
    const source = match ? sources[Number(match[1]) - 1] : undefined;
    const url = source ? safeHttpUrl(source.url) : null;
    return url
      ? <a className="inline-citation" key={`${part}-${index}`} href={url} target="_blank" rel="noreferrer" title={source?.title}>{part}</a>
      : <span key={`${part}-${index}`}>{part}</span>;
  })}</>;
}

function SourceList({ sources }: { sources: ResearchSource[] }) {
  if (!sources.length) return <p className="method-muted">登録された出典はありません。</p>;
  return <ol className="method-source-list">{sources.map((source, index) => {
    const url = safeHttpUrl(source.url);
    return <li key={`${source.url}-${index}`}>{url
      ? <a href={url} target="_blank" rel="noreferrer">{source.title || source.url} ↗</a>
      : <span>{source.title}</span>}</li>;
  })}</ol>;
}

function MethodCard({ method, run }: { method: ComparisonMethodKey; run?: ComparisonMethodRun }) {
  const contexts = researchContextOf(run);
  const sources = methodSources(run);
  const fields = controlledFields(run);
  const constraints = limitations(run);
  const rounds = searchRoundCount(run);
  const statusLabel = methodStatusLabel(method, run);
  const completed = run?.status === "completed" && Boolean(run.comparison)
    && !["Web検索未実行", "データ未取得", "一部未完了"].includes(statusLabel);

  return <article className="method-answer-card">
    <header className="method-answer-header">
      <div><span className="mini-label">{method === "data_context" ? "方式 1 / データ収集型" : "方式 2 / Web検索型"}</span>
        <h3>{method === "data_context" ? "データ収集型" : "Web検索型"}</h3></div>
      <span className={`method-status ${completed ? "is-complete" : "is-incomplete"}`}>{statusLabel}</span>
    </header>

    {run?.error && <p className="method-error" role="status">{run.error}</p>}
    {method === "data_context" ? <p className="method-explainer">担当Agentが公式・公開データを集め、指定した条件と確認可能な項目を回答へ反映します。</p>
      : <p className="method-explainer">1つのAgentがWeb検索を重ね、見つけた情報を引用付きで回答へまとめます。</p>}

    {method === "web_search" && <div className="method-round-count"><strong>{rounds.rounds}</strong><span>{rounds.maxRounds ? `/ ${rounds.maxRounds} 回` : "回"} 検索</span></div>}
    {method === "web_search" && methodStatusLabel(method, run) === "Web検索未実行" && <p className="method-offline-note">Web検索未実行。検索結果や出典を使った回答ではありません。</p>}

    <div className="method-premises">
      <div><span className="mini-label">指定・検証できる前提</span>
        {fields.length ? <ul>{fields.map((field) => <li key={field}>{field}</li>)}</ul> : <p>前提の記録はありません。</p>}
      </div>
      <div><span className="mini-label">確認できないこと・制約</span>
        {constraints.length ? <ul>{constraints.map((item) => <li key={item}>{item}</li>)}</ul> : <p>制約の記録はありません。</p>}
      </div>
    </div>

    {completed ? <>
      <div className="method-answer-body">
        <span className="mini-label">比較回答</span>
        <p><InlineCitations text={run.comparison!.narrative.summary} sources={sources} /></p>
        {!!run.comparison!.narrative.reasons.length && <ul>{run.comparison!.narrative.reasons.map((reason, index) => <li key={`${index}-${reason}`}><InlineCitations text={reason} sources={sources} /></li>)}</ul>}
        {!!run.comparison!.narrative.tradeoffs.length && <><strong>候補間で確認したい違い</strong><ul>{run.comparison!.narrative.tradeoffs.map((item, index) => <li key={`${index}-${item}`}><InlineCitations text={item} sources={sources} /></li>)}</ul></>}
        {!!run.comparison!.narrative.next_checks.length && <><strong>次に確認すること</strong><ul>{run.comparison!.narrative.next_checks.map((item, index) => <li key={`${index}-${item}`}><InlineCitations text={item} sources={sources} /></li>)}</ul></>}
      </div>
    </> : <div className="method-incomplete"><strong>この方式は未完了</strong><p>{run ? "回答を確定できる比較結果がありません。もう一方の方式と分けて表示しています。" : "この方式の結果を受信していません。"}</p></div>}

    {!!run?.candidates.length && <details className="method-city-details">
      <summary>都市ごとの回答・検索記録・出典を見る</summary>
      <div className="method-city-list">{run.candidates.map((candidate) => {
        const report = candidate.report;
        const context = report.research_context;
        const candidateSources = researchSources(context);
        return <article key={report.report_id}>
          <h4>{report.plan.region.name}</h4>
          <p><InlineCitations text={report.narrative.executive_summary} sources={candidateSources} /></p>
          {!!report.narrative.strengths.length && <><strong>確認した点</strong><ul>{report.narrative.strengths.map((item, index) => <li key={`${index}-${item}`}><InlineCitations text={item} sources={candidateSources} /></li>)}</ul></>}
          {!!report.narrative.cautions.length && <><strong>制約・注意点</strong><ul>{report.narrative.cautions.map((item, index) => <li key={`${index}-${item}`}><InlineCitations text={item} sources={candidateSources} /></li>)}</ul></>}
          {method === "web_search" && context?.steps.length ? <details className="method-search-steps">
            <summary>検索した内容（{context.steps.length} 回）</summary>
            <ol>{context.steps.map((step, index) => <li key={`${step.round}-${index}`}><strong>第{step.round}回：{step.query}</strong><p><InlineCitations text={step.summary} sources={context.sources} /></p><SourceList sources={step.sources ?? []} /></li>)}</ol>
          </details> : null}
          {!!candidateSources.length && <><strong>出典</strong><SourceList sources={candidateSources} /></>}
        </article>;
      })}</div>
    </details>}

    <details className="method-context-details">
      <summary>方式の前提・制約・出典を確認</summary>
      {contexts.map((context, index) => <div className="method-context-row" key={`${context.method}-${index}`}>
        <strong>{run?.candidates[index]?.report.plan.region.name ?? `候補 ${index + 1}`}</strong>
        <span>状態: {context.status === "verified" ? "確認済み" : context.status === "searched" ? "検索済み" : context.status === "offline" ? "未実行" : "失敗"}</span>
        {method === "web_search" && <span>検索回数: {context.search_rounds} / 最大{context.max_search_rounds}回</span>}
        <SourceList sources={researchSources(context)} />
      </div>)}
      {!contexts.length && <p>方式のメタデータはありません。過去に保存した結果では、この情報が記録されていない場合があります。</p>}
    </details>
  </article>;
}

export function MethodComparison({ methods, preference, regions, enabledAxes, weights }: {
  methods?: ComparisonMethods;
  preference: string;
  regions: string[];
  enabledAxes: AxisKey[];
  weights: Record<AxisKey, number>;
}) {
  if (!methods) return null;
  return <section className="method-comparison-panel" aria-labelledby="method-comparison-title">
    <span className="section-index">結果 / 方式比較</span>
    <h2 id="method-comparison-title">回答の前提をどこまで指定・検証できるか</h2>
    <p className="method-comparison-intro">2つの方式が同じ希望・都市・評価視点を受け取りました。回答と出典を左右で確認できます。方式間の優劣は自動で決めていません。</p>
    <div className="method-shared-conditions"><strong>実行時の共通条件</strong><span>{preference || "指定なし"}</span><span>{regions.join("・") || "都市情報なし"}</span><span>{enabledAxes.map(axis => `${AXIS_SHORT_LABELS[axis]} ${Math.round(weights[axis])}%`).join("・")}</span><small>条件や優先度を変える場合は、条件画面へ戻って再実行してください。</small></div>
    <div className="method-answer-grid">
      <MethodCard method="data_context" run={methods.data_context} />
      <MethodCard method="web_search" run={methods.web_search} />
    </div>
  </section>;
}
