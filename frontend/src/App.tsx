import { useEffect, useMemo, useState } from "react";
import { ARCHIVE_KEY, createComparison, downloadComparison, readArchive, type SavedComparison } from "./lib/archive";
import { runAssessment } from "./api";
import { createDemoReport } from "./demoReport";
import {
  AXIS_LABELS,
  AXIS_ORDER,
  AXIS_SHORT_LABELS,
  axisResultFor,
  formatElapsed,
  formatPercent,
  formatScore,
  normalizeWeights,
  reportReferenceDate,
  scoreTone,
  uniqueSources,
  weightedScore,
} from "./lib/report";
import type { AssessmentMode, AxisKey, CandidateReport, RunProgress } from "./types";

const DEFAULT_CANDIDATES = ["流山市", "柏市", "武蔵野市"];
const DEFAULT_WEIGHTS: Record<AxisKey, number> = {
  convenience: 20,
  housing: 20,
  family: 20,
  safety: 20,
  future: 20,
};

function initialCandidates(): CandidateReport[] {
  return DEFAULT_CANDIDATES.map((name) => ({
    report: createDemoReport(name),
    source: "demo",
    progress: ["開発用モックレポートを表示中"],
  }));
}

function parseCandidates(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[、,\n]/)
        .map((name) => name.trim())
        .filter(Boolean),
    ),
  ).slice(0, 4);
}

function candidateRequest(name: string, preference: string): string {
  const suffix = preference.trim() ? `。条件: ${preference.trim()}` : "";
  return `${name}の住みやすさを5軸で評価して${suffix}`;
}

function candidateModeLabel(candidate: CandidateReport): string {
  if (candidate.source === "knowledge" || candidate.report.plan.data_mode === "knowledge_only") {
    return candidate.report.execution_steps.some((step) => step.status === "fallback")
      ? "オフライン代替"
      : "LLM知識のみ";
  }
  if (candidate.source === "demo" || candidate.report.plan.data_mode === "mock") return "デモ表示";
  return "外部データ";
}

function App() {
  const [candidateInput, setCandidateInput] = useState(DEFAULT_CANDIDATES.join("、"));
  const [preference, setPreference] = useState("車なし・子育て・都内へ週3通勤");
  const [candidateReports, setCandidateReports] = useState(initialCandidates);
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [assessmentMode, setAssessmentMode] = useState<AssessmentMode>("data");
  const [selectedAxis, setSelectedAxis] = useState<AxisKey>("convenience");
  const [selectedCandidate, setSelectedCandidate] = useState(DEFAULT_CANDIDATES[0]);
  const [isRunning, setIsRunning] = useState(false);
  const [liveProgress, setLiveProgress] = useState<RunProgress[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [archive, setArchive] = useState<SavedComparison[]>([]);
  const [finished, setFinished] = useState<SavedComparison | null>(null);
  const [storageNotice, setStorageNotice] = useState("");
  useEffect(() => {
    try { setArchive(readArchive(localStorage)); }
    catch { setStorageNotice("保存履歴を読み込めませんでした。ダウンロードは利用できます。"); }
  }, []);

  const saveComparison = (comparison: SavedComparison) => {
    setFinished(comparison);
    const next = [comparison, ...archive.filter(item => item.id !== comparison.id)].slice(0, 10);
    try {
      localStorage.setItem(ARCHIVE_KEY, JSON.stringify(next));
      setArchive(next);
      setStorageNotice("このブラウザに保存しました（最新10件）。");
    } catch { setStorageNotice("ブラウザへ保存できませんでした。MarkdownまたはJSONをダウンロードしてください。"); }
  };
  const restoreComparison = (comparison: SavedComparison) => {
    setCandidateReports(comparison.candidates);
    setWeights(comparison.weights);
    setPreference(comparison.preference);
    setAssessmentMode(
      comparison.candidates.some((candidate) => candidate.report.plan.data_mode === "knowledge_only")
        ? "knowledge_only"
        : "data",
    );
    setCandidateInput(comparison.candidates.map(c => c.report.plan.region.name).join("、"));
    setSelectedCandidate(comparison.candidates[0].report.plan.region.name);
    setFinished(comparison);
  };

  const normalizedWeights = useMemo(() => normalizeWeights(weights), [weights]);
  const rankedCandidates = useMemo(
    () => [...candidateReports].sort((a, b) => weightedScore(b.report, weights) - weightedScore(a.report, weights)),
    [candidateReports, weights],
  );
  const winner = rankedCandidates[0];
  const runnerUp = rankedCandidates[1];
  const winnerScore = winner ? weightedScore(winner.report, weights) : 0;
  const runnerUpScore = runnerUp ? weightedScore(runnerUp.report, weights) : 0;
  const scoreGap = Math.round((winnerScore - runnerUpScore) * 10) / 10;
  const winnerBestAxes = winner
    ? [...winner.report.axis_results].sort((a, b) => b.score - a.score).slice(0, 2)
    : [];
  const winnerWatchAxes = winner
    ? [...winner.report.axis_results].sort((a, b) => a.score - b.score).slice(0, 2)
    : [];
  const activeCandidate =
    candidateReports.find((candidate) => candidate.report.plan.region.name === selectedCandidate) ??
    candidateReports[0];
  const selectedResult = activeCandidate
    ? axisResultFor(activeCandidate.report, selectedAxis)
    : undefined;
  const visibleProgress: RunProgress[] = isRunning
    ? liveProgress
    : (activeCandidate?.progress ?? []).map((message) => ({
        candidate: activeCandidate?.report.plan.region.name ?? "—",
        message,
      }));

  const handleWeightChange = (axis: AxisKey, value: number) => {
    setWeights((current) => ({ ...current, [axis]: value }));
  };

  const handleRun = async () => {
    const names = parseCandidates(candidateInput);
    if (!names.length) {
      setError("候補地を1つ以上入力してください。");
      return;
    }

    setError(null);
    setIsRunning(true);
    setFinished(null);
    setLiveProgress([]);
    setSelectedCandidate(names[0]);
    setCandidateReports(
      names.map((name): CandidateReport => ({
        report: createDemoReport(name),
        source: "demo",
        progress: ["APIへ接続しています…"],
      })),
    );

    try {
      const next = await Promise.all(
        names.map(async (name): Promise<CandidateReport> => {
          const progress: string[] = [];
          try {
            const response = await runAssessment(
              { request: candidateRequest(name, preference), mode: assessmentMode },
              (event) => {
                progress.push(event.message);
                setLiveProgress((current) => [...current.slice(-7), event]);
              },
            );
            return {
              report: response.report,
              markdown: response.markdown,
              source: assessmentMode === "knowledge_only" ? "knowledge" : "api",
              progress,
            };
          } catch (cause) {
            const message = cause instanceof Error ? cause.message : "API接続に失敗しました";
            progress.push(
              assessmentMode === "knowledge_only"
                ? `LLM知識モード利用不可: ${message}`
                : `データAPI利用不可: ${message}`,
            );
            return {
              report: createDemoReport(name),
              source: "demo",
              progress,
            };
          }
        }),
      );
      setCandidateReports(next);
      saveComparison(createComparison(next, weights, preference));
    } finally {
      setIsRunning(false);
    }
  };

  const displayScore = (candidate: CandidateReport) =>
    weightedScore(candidate.report, weights);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <span className="eyebrow">LIVABILITY / COMPARISON SURFACE</span>
          <h1>住む場所の比較</h1>
        </div>
        <div className="topbar-meta">
          <span className="status-chip">専用フロント</span>
          <span className="meta-mono">{assessmentMode === "knowledge_only" ? "LLM / KNOWLEDGE" : "DATA / API"}</span>
        </div>
      </header>

      <section className="query-panel panel-rule">
        <div className="query-copy">
          <span className="section-index">01 / INPUT</span>
          <h2>条件を置いて、候補地を比べる。</h2>
          <p>
            最後に「どこを第一候補にするか」まで表示します。評価軸の重みは後から動かせます。
          </p>
        </div>
        <div className="query-controls">
          <label className="field wide-field">
            <span>候補地</span>
            <input
              value={candidateInput}
              onChange={(event) => setCandidateInput(event.target.value)}
              placeholder="流山市、柏市、武蔵野市"
            />
            <small>読点・カンマ・改行で区切る / 最大4件</small>
          </label>
          <label className="field wide-field">
            <span>暮らしの条件</span>
            <input
              value={preference}
              onChange={(event) => setPreference(event.target.value)}
              placeholder="車なし・子育て・都内へ週3通勤"
            />
            <small>車なし、子育て、通勤頻度など。結論の前提になります。</small>
          </label>
          <div className="mode-field">
            <span className="mode-label">評価モード</span>
            <div className="mode-switch" role="group" aria-label="評価モード">
              <button
                type="button"
                className={assessmentMode === "data" ? "is-active" : ""}
                onClick={() => setAssessmentMode("data")}
                disabled={isRunning}
              >
                外部データあり
              </button>
              <button
                type="button"
                className={assessmentMode === "knowledge_only" ? "is-active" : ""}
                onClick={() => setAssessmentMode("knowledge_only")}
                disabled={isRunning}
              >
                LLM知識だけ
              </button>
            </div>
            <small>
              {assessmentMode === "knowledge_only"
                ? "地域データAPI・検索を使わず、LLMの一般知識だけで予備評価します。"
                : "サーバー設定の地域データソースを参照して評価します。"}
            </small>
          </div>
          <button className="run-button" onClick={handleRun} disabled={isRunning}>
            <span>{isRunning ? "評価中…" : "比較を実行"}</span>
            <span className="button-arrow">↗</span>
          </button>
        </div>
        {error ? <p className="error-note">{error}</p> : null}
      </section>

      {winner ? (
        <section className={`decision-hero ${candidateModeLabel(winner) === "外部データ" ? "decision-live" : "decision-caveat"}`} aria-labelledby="decision-title">
          <div className="decision-hero-main">
            <div className="decision-hero-copy">
              <span className="section-index">02 / DECISION</span>
              <span className="decision-kicker">今回の条件なら</span>
              <h2 id="decision-title">
                <strong>{winner.report.plan.region.name}</strong>を第一候補にします。
              </h2>
              <p>
                {runnerUp
                  ? `表示上の総合点は${formatScore(winnerScore)}点。次点の${runnerUp.report.plan.region.name}との差は${formatScore(scoreGap)}点です。`
                  : "候補を追加すると、候補間の差も表示します。"}
              </p>
              <div className="decision-tags">
                <span>{candidateModeLabel(winner)}</span>
                <span>重みで再計算</span>
                <span>候補 {rankedCandidates.length}件</span>
              </div>
            </div>
            <div className={`decision-score score-${scoreTone(winnerScore)}`}>
              <span>{formatScore(winnerScore)}</span>
              <small>/ 100</small>
              <em>第一候補スコア</em>
            </div>
          </div>
          <div className="decision-hero-grid">
            <div>
              <span className="mini-label">決め手</span>
              <ul>
                {winnerBestAxes.map((result) => <li key={result.axis}><strong>{result.label}</strong> {formatScore(result.score)}点 — {result.narrative.summary}</li>)}
              </ul>
            </div>
            <div className="decision-watch">
              <span className="mini-label">この2点だけ確認</span>
              <ul>
                {winnerWatchAxes.map((result) => <li key={result.axis}><strong>{result.label}</strong> {formatScore(result.score)}点 — {result.narrative.cautions[0] ?? "現地条件を確認"}</li>)}
              </ul>
            </div>
          </div>
          {candidateModeLabel(winner) !== "外部データ" ? (
            <p className="decision-disclaimer">
              {winner.report.plan.data_mode === "knowledge_only" && candidateModeLabel(winner) !== "オフライン代替"
                ? "LLM知識のみの予備判断です。最新の統計・検索結果ではないため、方向性を決める用途に限定してください。"
                : candidateModeLabel(winner) === "オフライン代替"
                  ? "LLM API未設定のため、外部データもLLMも使わないオフライン代替です。実在地域の意思決定には使えません。"
                  : "デモ値による表示です。実在地域の意思決定には使えません。"}
            </p>
          ) : null}
        </section>
      ) : null}

      <section className="workspace-grid">
        <div className="comparison-column">
          <div className="section-heading">
            <div>
              <span className="section-index">03 / COMPARISON</span>
              <h2>比較面</h2>
            </div>
            <div className="heading-note">
              <span className="live-mark" />
              {isRunning ? "実行中 — 進捗イベントを受信" : "データは候補地ごとに独立して評価"}
            </div>
          </div>

          <div className="comparison-table" style={{
            gridTemplateColumns: `minmax(190px, 1.2fr) repeat(${Math.max(candidateReports.length, 1)}, minmax(170px, 1fr))`,
          }}>
            <div className="table-corner">
              <span>評価軸</span>
              <small>重みを調整</small>
            </div>
            {candidateReports.map((candidate) => {
              const report = candidate.report;
              const name = report.plan.region.name;
              const score = displayScore(candidate);
              return (
                <button
                  key={name}
                  className={`candidate-header ${name === selectedCandidate ? "is-selected" : ""}`}
                  onClick={() => setSelectedCandidate(name)}
                >
                  <span className="candidate-name">{name}</span>
                  <span className={`overall-score score-${scoreTone(score)}`}>
                    {formatScore(score)}
                  </span>
                  <span className="candidate-meta">
                    {candidateModeLabel(candidate)} / 信頼度 {formatPercent(report.overall_confidence)}
                  </span>
                </button>
              );
            })}

            {AXIS_ORDER.map((axis) => (
              <div className="comparison-row" key={axis}>
                <button
                  className={`axis-label-cell ${axis === selectedAxis ? "is-selected" : ""}`}
                  onClick={() => setSelectedAxis(axis)}
                >
                  <span className="axis-name">{AXIS_LABELS[axis]}</span>
                  <span className="axis-weight">{Math.round(normalizedWeights[axis])}%</span>
                </button>
                {candidateReports.map((candidate) => {
                  const result = axisResultFor(candidate.report, axis);
                  const score = result?.score ?? 0;
                  return (
                    <button
                      className={`score-cell ${axis === selectedAxis ? "is-selected" : ""}`}
                      key={`${candidate.report.report_id}-${axis}`}
                      onClick={() => {
                        setSelectedAxis(axis);
                        setSelectedCandidate(candidate.report.plan.region.name);
                      }}
                    >
                      {result ? (
                        <>
                          <span className={`cell-score score-${scoreTone(score)}`}>{formatScore(score)}</span>
                          <span className="score-track">
                            <span className={`score-fill fill-${scoreTone(score)}`} style={{ width: `${score}%` }} />
                          </span>
                          <span className="cell-confidence">信頼度 {formatPercent(result.confidence)}</span>
                        </>
                      ) : (
                        <span className="excluded-cell">未実行</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="comparison-footnote">
            <span>点数は比較対象内の相対値。公式評価ではありません。</span>
            <span>クリックで下の根拠面を切り替え</span>
          </div>
        </div>

        <aside className="weight-panel panel-rule">
          <div className="section-heading compact-heading">
            <div>
              <span className="section-index">04 / WEIGHTS</span>
              <h2>表示上の重み</h2>
            </div>
          </div>
          <p className="aside-intro">
            いま見たい判断に合わせて、比較の中心を動かす。サーバーの元レポートは変更しません。
          </p>
          <div className="weight-list">
            {AXIS_ORDER.map((axis) => (
              <label className="weight-row" key={axis}>
                <span className="weight-label">{AXIS_SHORT_LABELS[axis]}</span>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="1"
                  value={weights[axis]}
                  onChange={(event) => handleWeightChange(axis, Number(event.target.value))}
                />
                <span className="weight-value">{Math.round(normalizedWeights[axis])}%</span>
              </label>
            ))}
          </div>
          <div className="weight-summary">
            <span>現在の見方</span>
            <strong>{activeCandidate ? activeCandidate.report.plan.region.name : "—"}</strong>
            <span>を基準に再計算</span>
          </div>
        </aside>
      </section>

      {activeCandidate ? (
        <section className="detail-section">
          <div className="section-heading">
            <div>
              <span className="section-index">05 / EVIDENCE</span>
              <h2>
                {activeCandidate.report.plan.region.name} / {AXIS_LABELS[selectedAxis]}
              </h2>
            </div>
            <div className="detail-meta">
              <span>{reportReferenceDate(activeCandidate.report)} 基準</span>
              <span>{uniqueSources(activeCandidate.report)} sources</span>
              <span>{formatElapsed(activeCandidate.report.total_elapsed_ms)}</span>
              <span>{candidateModeLabel(activeCandidate)}</span>
            </div>
          </div>

          <div className="detail-grid">
            <div className="narrative-block">
              <div className={`detail-score score-${scoreTone(selectedResult?.score ?? 0)}`}>
                {selectedResult ? formatScore(selectedResult.score) : "—"}
              </div>
              <p className="detail-summary">{selectedResult?.narrative.summary ?? "この軸は実行されていません。"}</p>
              <div className="strength-caution-grid">
                <div>
                  <span className="mini-label">強み</span>
                  <ul>
                    {(selectedResult?.narrative.strengths ?? []).map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
                <div>
                  <span className="mini-label">注意点</span>
                  <ul>
                    {(selectedResult?.narrative.cautions ?? []).map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              </div>
            </div>

            <div className="metrics-block">
              <div className="metrics-heading">
                <span>根拠になった指標</span>
                <span>source / reference date</span>
              </div>
              {selectedResult?.metrics.map((metric) => (
                <div className="metric-row" key={metric.metric_code}>
                  <div className="metric-main">
                    <strong>{metric.label}</strong>
                    <span>{metric.value} {metric.unit}</span>
                  </div>
                  <div className="metric-score">
                    <span className={`score-${scoreTone(metric.normalized_score)}`}>{formatScore(metric.normalized_score)}</span>
                    <span>{metric.source.source_id} / {metric.source.reference_date}</span>
                  </div>
                </div>
              ))}
              <div className="source-note">
                {selectedResult?.metrics[0] ? (
                  <>
                    <span className="mini-label">出典</span>
                    {selectedResult.metrics[0].source.url ? (
                      <a href={selectedResult.metrics[0].source.url} target="_blank" rel="noreferrer">
                        {selectedResult.metrics[0].source.source_name} ↗
                      </a>
                    ) : (
                      <span>{selectedResult.metrics[0].source.source_name}</span>
                    )}
                  </>
                ) : null}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      <section className="execution-section panel-rule">
        <div className="section-heading compact-heading">
          <div>
            <span className="section-index">06 / EXECUTION</span>
            <h2>実行記録</h2>
          </div>
          <div className="heading-note">会話履歴ではなく、評価の生成過程</div>
        </div>
        <div className="execution-layout">
          <div className="execution-steps">
            {(activeCandidate?.report.execution_steps ?? []).map((step) => (
              <div className="execution-step" key={step.name}>
                <span className={`step-state state-${step.status}`} />
                <div>
                  <strong>{step.name}</strong>
                  <span>{step.detail}</span>
                </div>
                <time>{formatElapsed(step.elapsed_ms)}</time>
              </div>
            ))}
          </div>
          <div className="live-log">
            <div className="metrics-heading">
              <span>直近の進捗</span>
              <span>{isRunning ? "streaming" : "idle"}</span>
            </div>
            {visibleProgress.slice(-6).map((event, index) => (
              <p key={`${event.message}-${index}`}>
                <span>{event.candidate}</span>
                {event.message}
              </p>
            ))}
            {!isRunning && !visibleProgress.length ? <p>実行するとここにイベントが流れます。</p> : null}
          </div>
        </div>
      </section>

      <section className="execution-section panel-rule report-section" aria-labelledby="report-title">
        <div className="section-heading compact-heading">
          <div>
            <span className="section-index">07 / REPORT</span>
            <h2 id="report-title">意思決定レポート</h2>
          </div>
          <div className="heading-note">比較結果をそのまま持ち出せる単体HTML</div>
        </div>
        <p>結論・ランキング・5軸の比較・根拠・注意点をひとつにまとめます。HTMLはメール添付や印刷にも使えます。</p>
        <p role="status">{storageNotice}</p>
        {finished && !isRunning && <>
          <div className="report-actions">
            <button className="report-primary-action" onClick={() => saveComparison(createComparison(candidateReports, weights, preference))}>現在の重みで保存</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, preference), "html")}>HTMLレポートをダウンロード</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, preference), "md")}>Markdown</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, preference), "json")}>JSON</button>
          </div>
          <details><summary>保存したレポートを表示</summary><pre className="report-preview">{finished.markdown}</pre></details>
        </>}
        {!finished && <p>{isRunning ? "調査完了後に保存・出力できます。" : "比較を実行するとレポートを保存します。"}</p>}
        <h3>保存履歴</h3>
        {!archive.length && <p>保存したレポートはありません。</p>}
        <ul>{archive.map(item => <li key={item.id}>
          <button disabled={isRunning} onClick={() => restoreComparison(item)}>{new Date(item.savedAt).toLocaleString("ja-JP")} / {item.candidates.map(c => c.report.plan.region.name).join("・")} を再表示</button>
        </li>)}</ul>
      </section>

      <footer className="app-footer">
        <span>開発用UI / mock data visible</span>
        <span>OpenWebUIの会話履歴は使用していません</span>
      </footer>
    </main>
  );
}

export default App;
