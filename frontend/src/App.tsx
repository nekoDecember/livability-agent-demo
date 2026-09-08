import { useMemo, useState } from "react";
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
import type { AxisKey, CandidateReport, RunProgress } from "./types";

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

function App() {
  const [candidateInput, setCandidateInput] = useState(DEFAULT_CANDIDATES.join("、"));
  const [preference, setPreference] = useState("車なし・子育て・都内へ週3通勤");
  const [candidateReports, setCandidateReports] = useState(initialCandidates);
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [selectedAxis, setSelectedAxis] = useState<AxisKey>("convenience");
  const [selectedCandidate, setSelectedCandidate] = useState(DEFAULT_CANDIDATES[0]);
  const [isRunning, setIsRunning] = useState(false);
  const [liveProgress, setLiveProgress] = useState<RunProgress[]>([]);
  const [error, setError] = useState<string | null>(null);

  const normalizedWeights = useMemo(() => normalizeWeights(weights), [weights]);
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
              { request: candidateRequest(name, preference) },
              (event) => {
                progress.push(event.message);
                setLiveProgress((current) => [...current.slice(-7), event]);
              },
            );
            return { report: response.report, source: "api", progress };
          } catch (cause) {
            const message = cause instanceof Error ? cause.message : "API接続に失敗しました";
            progress.push(`API利用不可: ${message}`);
            return {
              report: createDemoReport(name),
              source: "demo",
              progress,
            };
          }
        }),
      );
      setCandidateReports(next);
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
          <span className="meta-mono">SSE / API</span>
        </div>
      </header>

      <section className="query-panel panel-rule">
        <div className="query-copy">
          <span className="section-index">01 / INPUT</span>
          <h2>条件を置いて、候補地を比べる。</h2>
          <p>
            会話を残すのではなく、条件と根拠を残す。評価軸の重みは後から動かせます。
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
            <small>現在は評価リクエストへ付与する開発用の条件欄</small>
          </label>
          <button className="run-button" onClick={handleRun} disabled={isRunning}>
            <span>{isRunning ? "評価中…" : "比較を実行"}</span>
            <span className="button-arrow">↗</span>
          </button>
        </div>
        {error ? <p className="error-note">{error}</p> : null}
      </section>

      <section className="workspace-grid">
        <div className="comparison-column">
          <div className="section-heading">
            <div>
              <span className="section-index">02 / COMPARISON</span>
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
                    {candidate.source === "api" ? "API" : "DEMO"} / 信頼度 {formatPercent(report.overall_confidence)}
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
              <span className="section-index">03 / WEIGHTS</span>
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
              <span className="section-index">04 / EVIDENCE</span>
              <h2>
                {activeCandidate.report.plan.region.name} / {AXIS_LABELS[selectedAxis]}
              </h2>
            </div>
            <div className="detail-meta">
              <span>{reportReferenceDate(activeCandidate.report)} 基準</span>
              <span>{uniqueSources(activeCandidate.report)} sources</span>
              <span>{formatElapsed(activeCandidate.report.total_elapsed_ms)}</span>
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
                    <a href={selectedResult.metrics[0].source.url} target="_blank" rel="noreferrer">
                      {selectedResult.metrics[0].source.source_name} ↗
                    </a>
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
            <span className="section-index">05 / EXECUTION</span>
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

      <footer className="app-footer">
        <span>開発用UI / mock data visible</span>
        <span>OpenWebUIの会話履歴は使用していません</span>
      </footer>
    </main>
  );
}

export default App;
