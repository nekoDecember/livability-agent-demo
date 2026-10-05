import { useEffect, useMemo, useRef, useState } from "react";
import { ARCHIVE_KEY, createComparison, downloadComparison, readArchive, type SavedComparison } from "./lib/archive";
import { listRegions, runAssessment, runCommander, type RegionOption } from "./api";
import { SalesProposal, ProposalPresentation } from "./Proposal";
import { isMeasuredCandidate, proposalHeading, proposalStrength } from "./lib/proposal";
import { prioritizeAxis, suggestedWeights } from "./lib/preferences";
import { buildEvidenceSummary, comparisonAxisLabel } from "./lib/decision";
import {
  AXIS_LABELS,
  AXIS_ORDER,
  AXIS_SHORT_LABELS,
  axisResultFor,
  axisUnavailableLabel,
  formatElapsed,
  metricAvailabilityDetail,
  metricAvailabilityLabel,
  formatPercent,
  formatScore,
  normalizeWeights,
  reportReferenceDate,
  scoreTone,
  uniqueSources,
} from "./lib/report";
import type { AssessmentMode, AxisKey, CandidateComparison, CandidateReport, RunProgress } from "./types";

function candidateRequest(name: string, municipalityCode: string, preference: string, enabledAxes: AxisKey[]): string {
  const suffix = preference.trim() ? `。条件: ${preference.trim()}` : "";
  const axes = enabledAxes.map((axis) => AXIS_LABELS[axis]).join("・");
  return `対象自治体コード: ${municipalityCode}。${name}の住みやすさを${enabledAxes.length}軸（${axes}）で評価して${suffix}`;
}

function candidateModeLabel(candidate: CandidateReport): string {
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
  return ["外部データ", "公式公開データ"].includes(candidateModeLabel(candidate));
}

function weightContextSummary(
  profile: string,
  reasons: string[],
  priorityAxis: AxisKey | "",
  manual: boolean,
): string {
  const summary = manual ? "手動調整中。次の比較にもこの重みを使います。"
    : priorityAxis ? `${AXIS_LABELS[priorityAxis]}を優先します。共通指標がない軸は比較から外します。`
      : reasons.length ? `条件から自動設定: ${reasons.join("・")}。`
        : profile ? "入力条件に対応する採点可能な情報がないため、同じ割合で比較します。"
          : "条件指定がないため、各軸を同じ割合で比較します。";
  return /通勤|勤務地:/.test(profile.replaceAll("通勤なし", ""))
    ? `${summary} 通勤時間・経路は未測定で、点数化していません。`
    : summary;
}

const AXIS_SCORABLE_METRIC_COUNTS: Record<AxisKey, number> = {
  convenience: 5, housing: 4, family: 4, safety: 5, future: 4,
};

function catalogScorableMetricCount(): number {
  return Object.values(AXIS_SCORABLE_METRIC_COUNTS).reduce((sum, count) => sum + count, 0);
}

function sharedMetricCount(candidates: CandidateReport[], axis: AxisKey): number {
  if (!candidates.length) return 0;
  const sets = candidates.map((candidate) => new Set(
    (axisResultFor(candidate.report, axis)?.metrics ?? [])
      .filter((metric) => metric.quality > 0 && metric.direction !== "context_only")
      .map((metric) => metric.metric_code),
  ));
  return [...sets[0]].filter((metric) => sets.every((set) => set.has(metric))).length;
}

function App() {
  const [regions, setRegions] = useState<RegionOption[]>([]);
  const [selectedRegions, setSelectedRegions] = useState<RegionOption[]>([]);
  const [regionsUnavailable, setRegionsUnavailable] = useState(false);
  const [selectedPrefecture, setSelectedPrefecture] = useState("");
  const [selectedMunicipality, setSelectedMunicipality] = useState("");
  const [preference, setPreference] = useState("");
  const [age, setAge] = useState("");
  const [household, setHousehold] = useState("");
  const [commuteMode, setCommuteMode] = useState("");
  const [workplace, setWorkplace] = useState("");
  const [priorityAxis, setPriorityAxis] = useState<AxisKey | "">("");
  const [candidateReports, setCandidateReports] = useState<CandidateReport[]>([]);
  const [commanderAdvice, setCommanderAdvice] = useState<CandidateComparison | null>(null);
  const [commanderPending, setCommanderPending] = useState(false);
  const [commanderError, setCommanderError] = useState("");
  const [commanderRevision, setCommanderRevision] = useState(0);
  const [presentationOpen, setPresentationOpen] = useState(false);
  const [baselinePending, setBaselinePending] = useState(false);
  const [baselineError, setBaselineError] = useState("");
  const baselineRevision = useRef(0);
  const restoringProposal = useRef(false);
  const [view, setView] = useState<"research" | "results">("research");
  const [manualWeights, setManualWeights] = useState<Record<AxisKey, number> | null>(null);
  const [enabledAxes, setEnabledAxes] = useState<AxisKey[]>(AXIS_ORDER);
  const [assessmentMode, setAssessmentMode] = useState<AssessmentMode>("data");
  const [selectedAxis, setSelectedAxis] = useState<AxisKey>("convenience");
  const [selectedCandidate, setSelectedCandidate] = useState("");
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

  useEffect(() => {
    let active = true;
    listRegions().then((items) => {
      if (!active) return;
      setRegions(items);
      setRegionsUnavailable(false);
      setSelectedRegions([]);
      setSelectedPrefecture("");
      setSelectedMunicipality("");
    }).catch(() => {
      if (!active) return;
      setRegionsUnavailable(true);
      const fallback: RegionOption[] = [
        { name: "流山市", prefecture: "千葉県", municipality_code: "12220" },
        { name: "柏市", prefecture: "千葉県", municipality_code: "12217" },
        { name: "武蔵野市", prefecture: "東京都", municipality_code: "13203" },
      ];
      setRegions(fallback);
      setSelectedRegions([]);
      setSelectedPrefecture("");
      setSelectedMunicipality("");
    });
    return () => { active = false; };
  }, []);

  const prefectures = useMemo(
    () => Array.from(new Set(regions.map((item) => item.prefecture))).sort((a, b) => a.localeCompare(b, "ja")),
    [regions],
  );
  const municipalities = useMemo(
    () => regions.filter((item) => item.prefecture === selectedPrefecture).sort((a, b) => a.name.localeCompare(b.name, "ja")),
    [regions, selectedPrefecture],
  );
  const selectedNames = selectedRegions.map((item) => item.name);
  const addMunicipality = () => {
    const region = regions.find((item) => item.municipality_code === selectedMunicipality);
    if (!region || selectedRegions.some((item) => item.municipality_code === region.municipality_code) || selectedRegions.length >= 4) return;
    setSelectedRegions([...selectedRegions, region]);
    setFinished(null);
  };
  const removeMunicipality = (code: string) => {
    setSelectedRegions(selectedRegions.filter((item) => item.municipality_code !== code));
    setFinished(null);
  };

  const saveComparison = (comparison: SavedComparison) => {
    setFinished(comparison);
    setCommanderAdvice(comparison.commander ?? null);
    setView("results");
    const next = [comparison, ...archive.filter(item => item.id !== comparison.id)].slice(0, 10);
    try {
      localStorage.setItem(ARCHIVE_KEY, JSON.stringify(next));
      setArchive(next);
      setStorageNotice("このブラウザに保存しました（最新10件）。");
    } catch { setStorageNotice("ブラウザへ保存できませんでした。MarkdownまたはJSONをダウンロードしてください。"); }
  };
  const restoreComparison = (comparison: SavedComparison) => {
    restoringProposal.current = !!comparison.commander;
    const restoredAxes = AXIS_ORDER.filter((axis) =>
      comparison.candidates.some((candidate) => candidate.report.plan.enabled_axes.includes(axis)
        || candidate.report.plan.unavailable_axes?.includes(axis))
      || comparison.baseline?.candidates.some((candidate) => candidate.report.plan.enabled_axes.includes(axis)),
    );
    setCandidateReports(comparison.candidates);
    setCommanderAdvice(comparison.commander ?? null);
    setManualWeights(comparison.weights);
    setEnabledAxes(restoredAxes.length ? restoredAxes : AXIS_ORDER);
    setPreference(comparison.preference);
    setAge("");
    setHousehold("");
    setCommuteMode("");
    setWorkplace("");
    setPriorityAxis("");
    setAssessmentMode(
      comparison.candidates.some((candidate) => candidate.report.plan.data_mode === "knowledge_only")
        ? "knowledge_only"
        : "data",
    );
    setSelectedRegions(comparison.candidates.flatMap((candidate) => {
      const code = candidate.report.plan.region.municipality_code;
      const match = regions.find((region) => region.municipality_code === code);
      return match ? [match] : [];
    }));
    setSelectedCandidate(comparison.candidates[0].report.plan.region.municipality_code);
    setSelectedAxis(restoredAxes[0] ?? "convenience");
    setFinished(comparison);
    setView("results");
    setCommanderError("");
    setError(null);
  };

  const commuteCondition = commuteMode === "なし" ? "通勤なし" : commuteMode ? `${commuteMode}通勤` : "";
  const structuredConditions = [age && `${age}歳`, household, commuteCondition, workplace && `勤務地:${workplace}`].filter(Boolean).join("・");
  const effectivePreference = [structuredConditions, preference.trim()].filter(Boolean).join("・");
  const autoSuggestion = useMemo(() => suggestedWeights(effectivePreference), [effectivePreference]);
  const selectedWeights = useMemo(
    () => priorityAxis ? prioritizeAxis(autoSuggestion.weights, priorityAxis) : autoSuggestion.weights,
    [autoSuggestion, priorityAxis],
  );
  const weights = manualWeights ?? selectedWeights;
  const normalizedWeights = useMemo(
    () => normalizeWeights(weights, enabledAxes),
    [enabledAxes, weights],
  );
  const resultPreference = finished?.preference ?? effectivePreference;
  const decision = useMemo(
    () => buildEvidenceSummary(candidateReports, weights, resultPreference),
    [candidateReports, weights, resultPreference],
  );
  const sharedAxes = decision.sharedAxes;
  const sharedMetricTotal = AXIS_ORDER.reduce((sum, axis) => sum + sharedMetricCount(candidateReports, axis), 0);
  const catalogMetricTotal = catalogScorableMetricCount();
  const axesWithEvidence = AXIS_ORDER.filter((axis) => sharedMetricCount(candidateReports, axis) > 0).length;
  const displayedWeights = normalizedWeights;
  const winner = commanderAdvice?.recommended_region_code
    ? candidateReports.find((candidate) => candidate.report.plan.region.municipality_code === commanderAdvice.recommended_region_code) ?? null
    : null;
  const currentCommander = commanderPending ? undefined : commanderAdvice ?? undefined;
  const currentBaseline = JSON.stringify(finished?.weights) === JSON.stringify(weights) ? finished?.baseline : undefined;
  const proposalInput = { candidates: candidateReports, weights, preference: resultPreference, commander: currentCommander, baseline: currentBaseline };
  useEffect(() => {
    baselineRevision.current += 1;
    setBaselinePending(false);
    setBaselineError("");
  }, [candidateReports, weights, resultPreference, finished?.id]);
  const activeCandidate =
    candidateReports.find((candidate) => candidate.report.plan.region.municipality_code === selectedCandidate) ??
    candidateReports[0];
  const selectedResult = activeCandidate
    ? axisResultFor(activeCandidate.report, selectedAxis)
    : undefined;
  const selectedSourceMetric = selectedResult?.metrics.find((metric) => metric.quality > 0);
  const visibleProgress: RunProgress[] = liveProgress;

  useEffect(() => {
    if (restoringProposal.current) {
      restoringProposal.current = false;
      setCommanderPending(false);
      return;
    }
    if (!finished || candidateReports.length < 2 || isRunning) {
      setCommanderPending(false);
      return;
    }
    let active = true;
    setCommanderPending(true);
    setCommanderError("");
    const timer = window.setTimeout(() => {
      void runCommander(
        candidateReports.map((candidate) => candidate.report),
        resultPreference || "指定なし",
        normalizeWeights(weights, enabledAxes),
      ).then((advice) => {
        if (!active) return;
        setCommanderAdvice(advice);
        if (advice.recommended_region_code) setSelectedCandidate(advice.recommended_region_code);
        const current = finished;
        const refreshed = createComparison(
          candidateReports,
          weights,
          resultPreference,
          advice,
          JSON.stringify(current.weights) === JSON.stringify(weights) ? current.baseline : undefined,
        );
        refreshed.id = current.id;
        refreshed.savedAt = current.savedAt;
        setFinished(refreshed);
        setArchive((previous) => {
          const next = [refreshed, ...previous.filter((item) => item.id !== refreshed.id)].slice(0, 10);
          try {
            localStorage.setItem(ARCHIVE_KEY, JSON.stringify(next));
            setStorageNotice("司令塔の提案を保存履歴へ反映しました。");
          } catch {
            setStorageNotice("提案は表示しました。保存履歴の更新には失敗しました。");
          }
          return next;
        });
      }).catch((cause: unknown) => {
        if (active) {
          setCommanderAdvice(null);
          setCommanderError(cause instanceof Error ? cause.message : "司令塔の統合提案を取得できませんでした。");
        }
      }).finally(() => {
        if (active) setCommanderPending(false);
      });
    }, 500);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [candidateReports, decision.sharedAxes, enabledAxes, finished?.id, isRunning, resultPreference, weights, commanderRevision]);

  const handleBaselineComparison = async () => {
    if (!finished || !currentCommander || commanderPending || !candidateReports.every(isMeasuredCandidate)) return;
    const revision = ++baselineRevision.current;
    const snapshot = finished;
    setBaselinePending(true);
    setBaselineError("");
    try {
      const candidates = await Promise.all(candidateReports.map(async (candidate): Promise<CandidateReport> => {
        const region = candidate.report.plan.region;
        const response = await runAssessment({
          request: candidateRequest(region.name, region.municipality_code, resultPreference, enabledAxes),
          enabled_axes: enabledAxes,
          weights: Object.fromEntries(enabledAxes.map(axis => [axis, normalizedWeights[axis]])),
          mode: "knowledge_only",
        }, () => {});
        return { report: response.report, markdown: response.markdown, source: "knowledge", progress: [] };
      }));
      const commander = await runCommander(candidates.map(candidate => candidate.report), resultPreference || "指定なし", normalizedWeights);
      if (baselineRevision.current !== revision) return;
      const comparison = createComparison(candidateReports, weights, resultPreference, currentCommander, { candidates, commander });
      comparison.id = snapshot.id;
      comparison.savedAt = snapshot.savedAt;
      saveComparison(comparison);
    } catch (cause) {
      if (baselineRevision.current === revision) setBaselineError(cause instanceof Error ? cause.message : "データなしの比較を作成できませんでした。再実行してください。");
    } finally {
      if (baselineRevision.current === revision) setBaselinePending(false);
    }
  };

  const handleWeightChange = (axis: AxisKey, value: number) => {
    setManualWeights(normalizeWeights({ ...displayedWeights, [axis]: value }, enabledAxes));
  };

  const updateCondition = (update: () => void) => {
    update();
    setManualWeights(null);
    setFinished(null);
  };

  const handleAxisToggle = (axis: AxisKey) => {
    const isEnabled = enabledAxes.includes(axis);
    if (isEnabled && enabledAxes.length === 1) {
      setError("評価軸は少なくとも1つ有効にしてください。");
      return;
    }

    const next = AXIS_ORDER.filter((candidate) =>
      candidate === axis ? !isEnabled : enabledAxes.includes(candidate),
    );
    setEnabledAxes(next);
    setFinished(null);
    if (isEnabled && selectedAxis === axis) setSelectedAxis(next[0]);
    setError(null);
  };

  const handleRun = async () => {
    const names = selectedRegions.map((item) => item.name);
    if (names.length < 2) {
      setError("比較する市区町村を2件以上選んでください。");
      return;
    }

    setError(null);
    setIsRunning(true);
    setCommanderAdvice(null);
    baselineRevision.current += 1;
    setBaselinePending(false);
    setBaselineError("");
    setCommanderError("");
    setCommanderPending(false);
    setFinished(null);
    setView("research");
    setLiveProgress([]);
    setSelectedCandidate(selectedRegions[0].municipality_code);

    try {
      const settled = await Promise.allSettled(
        selectedRegions.map(async (region): Promise<CandidateReport> => {
          const name = region.name;
          const progress: string[] = [];
          try {
            const response = await runAssessment(
              {
                request: candidateRequest(name, region.municipality_code, effectivePreference, enabledAxes),
                enabled_axes: enabledAxes,
                weights: Object.fromEntries(enabledAxes.map(axis => [axis, normalizedWeights[axis]])),
                mode: assessmentMode,
              },
              (event) => {
                progress.push(event.message);
                setLiveProgress((current) => [...current.slice(-11), { candidate: name, message: event.message }]);
              },
            );
            setLiveProgress((current) => [...current.slice(-11), { candidate: name, message: "調査が完了しました" }]);
            return {
              report: response.report,
              markdown: response.markdown,
              source: assessmentMode === "knowledge_only" ? "knowledge" : "api",
              progress,
            };
          } catch (cause) {
            setLiveProgress((current) => [...current.slice(-11), { candidate: name, message: "調査に失敗しました" }]);
            throw cause;
          }
        }),
      );
      const failed = settled.flatMap((result, index) => result.status === "rejected"
        ? [`${names[index]}: ${result.reason instanceof Error ? result.reason.message : "調査に失敗しました"}`]
        : []);
      if (failed.length) {
        setError(`結果はまだ確定していません。${failed.join(" / ")}。条件を確認して再実行してください。`);
        return;
      }
      const next = settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
      setCandidateReports(next);
      setSelectedCandidate(next[0]?.report.plan.region.municipality_code ?? "");
      saveComparison(createComparison(next, weights, effectivePreference));
      setView("results");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "調査結果を保存できませんでした。再実行してください。");
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <main className="app-shell">
      {presentationOpen && currentCommander && <ProposalPresentation input={proposalInput} onClose={() => setPresentationOpen(false)} />}
      <header className="topbar">
        <div className="brand-block">
          <span className="eyebrow">LIVABILITY / LIVING ADVISER</span>
          <h1>あなたの希望から、選ぶ都市をご提案</h1>
        </div>
        <div className="topbar-meta">
          <span className="status-chip">{view === "research" ? "調査画面" : "結果画面"}</span>
          <span className="meta-mono">{assessmentMode === "knowledge_only" ? "LLM / KNOWLEDGE" : "DATA / PROVIDER"}</span>
        </div>
      </header>

      <nav className="view-nav" aria-label="画面切り替え">
        <button type="button" className={view === "research" ? "is-active" : ""} onClick={() => setView("research")}>01 条件・調査</button>
        <button type="button" className={view === "results" ? "is-active" : ""} disabled={!finished || isRunning} onClick={() => setView("results")}>02 都市選びのご提案</button>
      </nav>

      {view === "research" ? <>
      <section className="query-panel panel-rule">
        <div className="query-copy">
          <span className="section-index">01 / INPUT</span>
          <h2>どんな暮らしをしたいですか？</h2>
          <p>
            暮らしの条件から見るべき情報を決め、候補地ごとの調査結果を統合して提案します。
          </p>
        </div>
        <div className="query-controls">
          <div className="field wide-field city-picker">
            <span>比較する都市</span>
            {selectedNames.length > 0 ? <div className="city-chips" aria-label="選択中の候補地">
              {selectedRegions.map((region) => <span className="city-chip" key={region.municipality_code}><span>{region.name}<small>{region.prefecture} · {region.municipality_code}</small></span><button type="button" aria-label={`${region.name}を削除`} onClick={() => removeMunicipality(region.municipality_code)} disabled={isRunning}>×</button></span>)}
            </div> : <small>候補地を選んでください。</small>}
            <div className="city-picker-controls">
              <label>
                <span className="sr-only">都道府県</span>
                <select value={selectedPrefecture} onChange={(event) => {
                  const next = event.target.value;
                  setSelectedPrefecture(next);
                  setSelectedMunicipality("");
                }} disabled={!regions.length || isRunning}>
                  <option value="">都道府県を選択</option>
                  {prefectures.map((prefecture) => <option key={prefecture} value={prefecture}>{prefecture}</option>)}
                </select>
              </label>
              <label>
                <span className="sr-only">市区町村</span>
                <select value={selectedMunicipality} onChange={(event) => setSelectedMunicipality(event.target.value)} disabled={!municipalities.length || isRunning}>
                  <option value="">市区町村を選択</option>
                  {municipalities.map((region) => <option key={region.municipality_code} value={region.municipality_code}>{region.name}</option>)}
                </select>
              </label>
              <button type="button" className="add-city-button" onClick={addMunicipality} disabled={!selectedMunicipality || selectedRegions.some((item) => item.municipality_code === selectedMunicipality) || selectedRegions.length >= 4 || isRunning}>都市を追加</button>
            </div>
            <small>{regionsUnavailable ? "地域一覧に接続できません。API接続後に比較できます。" : `データ収録済みの自治体から2～4件選択（${selectedNames.length}/4）`}</small>
          </div>
          <div className="field wide-field lifestyle-fields">
            <span>暮らしの条件</span>
            <div className="structured-condition-grid">
              <label><span>年齢</span><input inputMode="numeric" value={age} onChange={(event) => updateCondition(() => setAge(event.target.value.replace(/\D/g, "").slice(0, 3)))} placeholder="25" aria-label="年齢" /></label>
              <label><span>世帯</span><select value={household} onChange={(event) => updateCondition(() => setHousehold(event.target.value))} aria-label="世帯"><option value="">選択なし</option><option>独身</option><option>夫婦</option><option>子育て世帯</option><option>一人暮らし</option></select></label>
              <label><span>通勤手段</span><select value={commuteMode} onChange={(event) => updateCondition(() => setCommuteMode(event.target.value))} aria-label="通勤手段"><option value="">指定なし</option><option>車</option><option>鉄道</option><option>その他</option><option>なし</option></select></label>
              <label><span>勤務地（任意）</span><input value={workplace} onChange={(event) => updateCondition(() => setWorkplace(event.target.value))} placeholder="高崎駅周辺" aria-label="勤務地" /></label>
            </div>
            <label className="free-condition"><span>その他の希望（任意）</span><input value={preference} onChange={(event) => updateCondition(() => setPreference(event.target.value))} placeholder="家賃を抑えたい・休日は自然の中で過ごしたい" /></label>
            <small>入力した条件を専門Agentの調査と司令塔の提案に使います。実通勤時間や物件価格など未取得の情報は、確認事項として示します。</small>
          </div>
          <div className="mode-field">
            <span className="mode-label">評価モード</span>
            <div className="mode-switch" role="group" aria-label="評価モード">
              <button
                type="button"
                className={assessmentMode === "data" ? "is-active" : ""}
                onClick={() => { setAssessmentMode("data"); setFinished(null); }}
                disabled={isRunning}
              >
                データ評価
              </button>
              <button
                type="button"
                className={assessmentMode === "knowledge_only" ? "is-active" : ""}
                onClick={() => { setAssessmentMode("knowledge_only"); setFinished(null); }}
                disabled={isRunning}
              >
                LLM知識評価
              </button>
            </div>
            <small>
              {assessmentMode === "knowledge_only"
                ? "ナレッジON: 地域データAPI・検索を使わず、LLMの一般知識だけで予備評価します。"
                : "ナレッジOFF: 地域データを使い、LLMの一般知識で数値を補完しません。"}
            </small>
          </div>
          <button className="run-button" onClick={handleRun} disabled={isRunning || selectedRegions.length < 2 || regionsUnavailable}>
            <span>{isRunning ? "調査・提案中…" : "この条件で提案をつくる"}</span>
            <span className="button-arrow">↗</span>
          </button>
          <details className="advanced-settings">
            <summary>評価の詳細設定（調査の重点・範囲）</summary>
            <label className="priority-field"><span>今回の調査で特に重視する視点</span><select value={priorityAxis} onChange={(event) => { setPriorityAxis(event.target.value as AxisKey | ""); setManualWeights(null); setFinished(null); }}>
              <option value="">暮らしの条件から自動設定</option>
              {AXIS_ORDER.map((axis) => <option key={axis} value={axis}>{AXIS_LABELS[axis]}</option>)}
            </select></label>
            <small>重みは司令塔へ伝える優先度です。候補を機械的に決めず、専門Agentの根拠と暮らしの条件を合わせて提案します。</small>
            <div className="axis-toggle-field">
              <span className="mode-label">調査する視点（オン／オフ）</span>
              <div className="axis-toggle-list" role="group" aria-label="評価軸">
                {AXIS_ORDER.map((axis) => {
                  const isEnabled = enabledAxes.includes(axis);
                  return (
                    <button
                      type="button"
                      key={axis}
                      className={isEnabled ? "is-active" : ""}
                      aria-pressed={isEnabled}
                      onClick={() => handleAxisToggle(axis)}
                      disabled={isRunning}
                    >
                      <span aria-hidden="true">{isEnabled ? "✓" : "—"}</span>
                      {AXIS_SHORT_LABELS[axis]}
                    </button>
                  );
                })}
              </div>
              <small>有効にした視点の専門Agentが各候補地を調べ、司令塔が共通点・違い・未確認事項をまとめます。</small>
            </div>
          </details>
        </div>
        {error ? <p className="error-note">{error}</p> : null}
      </section>

      <section className="research-status panel-rule" aria-live="polite">
        <span className="section-index">02 / RESEARCH</span>
        <h2>{isRunning ? "各候補地を調査しています" : error ? "調査を完了できませんでした" : finished ? "前回の結果を確認できます" : "調査の準備ができました"}</h2>
        <p>{isRunning ? "候補地ごとの専門Agentが根拠を調べ、司令塔が暮らしの条件に合わせた提案をまとめています。" : "専門Agentの調査結果を司令塔がまとめ、暮らし方に合った提案を返します。軸別の数値は根拠として後から確認できます。"}</p>
        <div className="research-candidates">
          {selectedRegions.map((region) => <div key={region.municipality_code} className="research-candidate">
            <strong>{region.name}</strong><span>{liveProgress.some((event) => event.candidate === region.name && event.message === "調査に失敗しました") ? "失敗" : liveProgress.some((event) => event.candidate === region.name && event.message === "調査が完了しました") ? "調査完了" : isRunning ? "調査中" : "待機中"}</span>
          </div>)}
        </div>
        <div className="live-log research-log">
          {visibleProgress.slice(-8).map((event, index) => <p key={`${event.candidate}-${index}`}><span>{event.candidate}</span>{event.message}</p>)}
        </div>
        <h3>今回の提案で見るポイント</h3>
        <p>{weightContextSummary(effectivePreference, autoSuggestion.reasons, priorityAxis, Boolean(manualWeights))}</p>
        <div className="research-weights">{enabledAxes.map((axis) => <span key={axis}>{AXIS_SHORT_LABELS[axis]} {Math.round(normalizedWeights[axis])}%</span>)}</div>
        {manualWeights && <button type="button" onClick={() => setManualWeights(null)}>条件から自動設定に戻す</button>}
        {finished && <button type="button" onClick={() => setView("results")}>前回の結果を見る →</button>}
        {archive.length > 0 && <details><summary>保存した結果を開く</summary><ul>{archive.map((item) => <li key={item.id}><button disabled={isRunning} onClick={() => restoreComparison(item)}>{new Date(item.savedAt).toLocaleString("ja-JP")} / {item.candidates.map((candidate) => candidate.report.plan.region.name).join("・")}</button></li>)}</ul></details>}
      </section>
      </> : <>

      {commanderPending ? (
        <section className="decision-hero decision-caveat" aria-labelledby="decision-title" aria-live="polite">
          <span className="section-index">RESULT / SYNTHESIS</span>
          <h2 id="decision-title">あなたに合う街の提案をまとめています。</h2>
          <p>5軸の専門Agentが集めた候補地ごとの根拠と、入力された生活条件を統合しています。</p>
          <p className="commander-status">数値の順位ではなく、今回の条件で何を選び、何を確認するかを整理しています…</p>
        </section>
      ) : winner ? (
        <section className={`decision-hero ${hasMeasuredData(winner) ? "decision-live" : "decision-caveat"}`} aria-labelledby="decision-title">
          <div className="decision-hero-main">
            <div className="decision-hero-copy">
              <span className="section-index">RESULT / COMMANDER PROPOSAL</span>
              <span className="decision-kicker">{proposalStrength(candidateReports, currentCommander)}</span>
              <h2 id="decision-title">{proposalHeading(candidateReports, currentCommander)}</h2>
              <p>{commanderAdvice?.narrative.summary}</p>
              <p className="profile-summary">暮らしの条件: {resultPreference || "指定なし"}</p>
              <div className="decision-priorities" aria-label="司令塔へ伝えた視点の優先割合">
                <span className="mini-label">あなたが大切にしたいこと</span>
                <div>{enabledAxes.map((axis) => <span key={axis}><b>{AXIS_SHORT_LABELS[axis]}</b>{Math.round(normalizedWeights[axis])}%</span>)}</div>
              </div>
              <div className="decision-tags">
                <span>{candidateModeLabel(winner)}</span>
                <span>{commanderAdvice?.used_fallback ? "ルールによる暫定代替" : "希望に合わせて候補を選定"}</span>
                <span>暮らしの希望と候補間の比較を統合</span>
              </div>
            </div>
          </div>
          <div className="decision-hero-grid">
            <div>
              <span className="mini-label">この候補を提案する理由</span>
              <ul>{(commanderAdvice?.narrative.reasons ?? []).map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul>
            </div>
            <div className="decision-watch">
              <span className="mini-label">選ぶ前に知っておきたい違い</span>
              <ul>{(commanderAdvice?.narrative.tradeoffs ?? []).map((tradeoff, index) => <li key={`${index}-${tradeoff}`}>{tradeoff}</li>)}</ul>
            </div>
          </div>
          <div className="decision-next-checks">
            <span className="mini-label">次に確かめること</span>
            <ul>{(commanderAdvice?.narrative.next_checks ?? []).map((check, index) => <li key={`${index}-${check}`}>{check}</li>)}</ul>
          </div>
          <p className="decision-disclaimer">市全体の統計は街の傾向を示します。住居ごとの距離・空き・価格は「次に確かめること」をご覧ください。</p>
          {!hasMeasuredData(winner) ? <p className="decision-disclaimer">{candidateModeLabel(winner)}による調査前の仮説です。都市選びの方向性を考えた後、一次情報で確認してください。</p> : null}
        </section>
      ) : candidateReports.length ? (
        <section className="decision-hero decision-caveat" aria-labelledby="decision-title">
          <span className="section-index">RESULT / NO SINGLE RECOMMENDATION</span>
          <h2 id="decision-title">{commanderAdvice?.narrative.summary ?? (commanderError ? "司令塔の統合提案を取得できませんでした。" : "候補を一つに絞るだけの根拠がありません。")}</h2>
          {commanderAdvice?.narrative.reasons.length ? <ul>{commanderAdvice.narrative.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>{commanderError || decision.reason}</p>}
          {commanderAdvice?.narrative.tradeoffs.length ? <><span className="mini-label">候補ごとの違い</span><ul>{commanderAdvice.narrative.tradeoffs.map((item) => <li key={item}>{item}</li>)}</ul></> : null}
          {commanderAdvice?.narrative.next_checks.length ? <><span className="mini-label">次に確かめること</span><ul>{commanderAdvice.narrative.next_checks.map((item) => <li key={item}>{item}</li>)}</ul></> : null}
          {commanderError ? <button type="button" onClick={() => setCommanderRevision((revision) => revision + 1)}>司令塔の提案を再試行</button> : null}
        </section>
      ) : null}

      {currentCommander && <>
        <div className="sales-toolbar"><button type="button" onClick={() => setPresentationOpen(true)}>提案をプレゼンで見る</button><span>結論 → 選ぶ理由 → 比較根拠 → 他候補 → 次の行動</span></div>
        <SalesProposal input={proposalInput} comparing={baselinePending} error={baselineError} onCompare={() => void handleBaselineComparison()} />
      </>}
      <details className="evidence-disclosure">
        <summary>調査根拠・5つの視点・割合の内訳を見る</summary>
      <section className="data-coverage" aria-label="データ取得状況">
        <div>
          <span className="section-index">DATA COVERAGE</span>
          <h2>司令塔が参照できた情報</h2>
          <p>{axesWithEvidence}/5視点に共通データがあります。採点対象22指標のうち、両市で取得できた数は{sharedMetricTotal}です。背景情報と未取得値は採点に含めません。</p>
        </div>
        <strong className="coverage-total">{sharedMetricTotal}<small> / {catalogMetricTotal} 指標</small></strong>
        <div className="coverage-list">
          {AXIS_ORDER.map((axis) => {
            const count = sharedMetricCount(candidateReports, axis);
            return <span key={axis} className={count === 0 ? "is-empty" : ""}><b>{AXIS_SHORT_LABELS[axis]}</b>{count}/{AXIS_SCORABLE_METRIC_COUNTS[axis]}</span>;
          })}
        </div>
        <small className="context-coverage-note">大学・短大・高専キャンパス数など、採点対象外の背景情報はこの数に含めていません。</small>
      </section>

      <section className="workspace-grid">
        <div className="comparison-column">
          <div className="section-heading">
            <div>
              <span className="section-index">03 / COMPARISON</span>
              <h2>候補地ごとの根拠</h2>
            </div>
            <div className="heading-note">
              <span className="live-mark" />
              同じ指標が揃う軸だけを比較
            </div>
          </div>

          <div className="comparison-table" style={{
            gridTemplateColumns: `minmax(190px, 1.2fr) repeat(${Math.max(candidateReports.length, 1)}, minmax(170px, 1fr))`,
          }}>
            <div className="table-corner">
              <span>評価軸</span>
              <small>優先度を調整</small>
            </div>
            {candidateReports.map((candidate) => {
              const report = candidate.report;
              const name = report.plan.region.name;
              return (
                <button
                  key={report.plan.region.municipality_code}
                  className={`candidate-header ${report.plan.region.municipality_code === selectedCandidate ? "is-selected" : ""}`}
                  onClick={() => setSelectedCandidate(report.plan.region.municipality_code)}
                >
                  <span className="candidate-name">{name}</span>
                  <span className="candidate-meta">
                    {candidateModeLabel(candidate)} / 根拠の確度 {formatPercent(report.research_confidence)}
                  </span>
                </button>
              );
            })}

            {enabledAxes.map((axis) => (
              <div className="comparison-row" key={axis}>
                <button
                  className={`axis-label-cell ${axis === selectedAxis ? "is-selected" : ""}`}
                  onClick={() => setSelectedAxis(axis)}
                >
                  <span className="axis-name">{comparisonAxisLabel(candidateReports, axis)}</span>
                  <span className="axis-weight">{sharedAxes.includes(axis) ? `${Math.round(displayedWeights[axis])}%` : "採点外"}</span>
                </button>
                {candidateReports.map((candidate) => {
                  const result = axisResultFor(candidate.report, axis);
                  const score = result?.score ?? 0;
                  const verdict = decision.axisVerdicts.find((item) => item.axis === axis);
                  const isLeader = verdict?.leaders.some((item) => item.report.plan.region.municipality_code === candidate.report.plan.region.municipality_code) ?? false;
                  return (
                    <button
                      className={`score-cell ${axis === selectedAxis ? "is-selected" : ""}`}
                      key={`${candidate.report.report_id}-${axis}`}
                      onClick={() => {
                        setSelectedAxis(axis);
                        setSelectedCandidate(candidate.report.plan.region.municipality_code);
                      }}
                    >
                      {result && sharedAxes.includes(axis) ? (
                        <>
                          <span className={`axis-verdict ${isLeader && !verdict?.nearTie ? "axis-verdict-lead" : ""}`}>{verdict?.nearTie ? isLeader ? `僅差（${formatScore(verdict.gap)}点差）` : `${verdict.leaders.map((item) => item.report.plan.region.name).join("・")}が僅差で上位` : isLeader ? "この軸で優勢" : `${verdict?.leaders.map((item) => item.report.plan.region.name).join("・")}が優勢`}</span>
                          <span className="cell-confidence">指標スコア {formatScore(score)} / 信頼度 {formatPercent(result.confidence)}</span>
                        </>
                      ) : result ? (
                        <span className="excluded-cell">共通指標が揃わず比較外</span>
                      ) : (
                        <span className="excluded-cell">{axisUnavailableLabel(candidate.report, axis)}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="comparison-footnote">
            <span>この表は司令塔の提案を確認するための根拠です。軸別の点数だけで順位は決まりません。</span>
            <span>クリックで出典と欠損理由を見る</span>
          </div>
        </div>

        <aside className="weight-panel panel-rule">
          <div className="section-heading compact-heading">
            <div>
              <span className="section-index">04 / WEIGHTS</span>
              <h2>暮らしに合わせた重み</h2>
            </div>
          </div>
          <p className="aside-intro">
            {weightContextSummary(resultPreference, autoSuggestion.reasons, priorityAxis, Boolean(manualWeights))}
          </p>
          {manualWeights && <button type="button" onClick={() => setManualWeights(null)}>条件から自動設定に戻す</button>}
          <div className="weight-list">
            {enabledAxes.map((axis) => (
              <label className="weight-row" key={axis}>
                <span className="weight-label">{AXIS_SHORT_LABELS[axis]}</span>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="1"
                  value={displayedWeights[axis]}
                  onChange={(event) => handleWeightChange(axis, Number(event.target.value))}
                />
                <span className="weight-value">{Math.round(displayedWeights[axis])}%</span>
              </label>
            ))}
          </div>
          <div className="weight-summary">
            <span>司令塔へ伝えた共通データ</span>
            <strong>{sharedMetricTotal}/{catalogMetricTotal}</strong>
            <span>指標</span>
          </div>
        </aside>
      </section>

      {activeCandidate ? (
        <section className="detail-section">
          <div className="section-heading">
            <div>
              <span className="section-index">05 / EVIDENCE</span>
              <h2>
                {activeCandidate.report.plan.region.name} / {comparisonAxisLabel(candidateReports, selectedAxis)}
              </h2>
            </div>
            <div className="detail-meta">
              <span>基準年: {reportReferenceDate(activeCandidate.report)}</span>
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
                <div className={`metric-row ${metric.quality <= 0 ? "metric-unavailable" : ""}`} key={metric.metric_code}>
                  <div className="metric-main">
                    <strong>{metric.label}</strong>
                    <span>{metric.quality > 0 ? `${metric.value} ${metric.unit}` : metricAvailabilityLabel(metric)}</span>
                    {metric.direction === "context_only" && metric.note ? (
                      <small className="metric-note">{metric.note}</small>
                    ) : null}
                  </div>
                  <div className="metric-score">
                    {metric.direction === "context_only" ? (
                      <span>背景情報・採点対象外</span>
                    ) : metric.quality > 0 ? (
                      <span className={`score-${scoreTone(metric.normalized_score)}`}>{formatScore(metric.normalized_score)}</span>
                    ) : <span>{metricAvailabilityDetail(metric)}</span>}
                    <span>{metric.source.source_id} / {metric.source.reference_date}</span>
                  </div>
                </div>
              ))}
              <div className="source-note">
                {selectedSourceMetric ? (
                  <>
                    <span className="mini-label">出典</span>
                    {selectedSourceMetric.source.url ? (
                      <a href={selectedSourceMetric.source.url} target="_blank" rel="noreferrer">
                        {selectedSourceMetric.source.source_name} ↗
                      </a>
                    ) : (
                      <span>{selectedSourceMetric.source.source_name}</span>
                    )}
                  </>
                ) : null}
              </div>
              {selectedSourceMetric ? (
                <p className="source-license-note">
                  {selectedSourceMetric.source.commercial_use_note}
                </p>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}
      </details>

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
        </div>
      </section>

      <section className="execution-section panel-rule report-section" aria-labelledby="report-title">
        <div className="section-heading compact-heading">
          <div>
            <span className="section-index">07 / REPORT</span>
            <h2 id="report-title">都市選びの提案レポート</h2>
          </div>
          <div className="heading-note">比較結果をそのまま持ち出せる単体HTML</div>
        </div>
        <p>選ぶ街、軸ごとの優劣、受け入れる弱点と確認事項をまとめます。HTMLはメール添付や印刷にも使えます。</p>
        <p role="status">{storageNotice}</p>
        {finished && !isRunning && !commanderPending && currentCommander && <>
          <div className="report-actions">
            <button className="report-primary-action" onClick={() => saveComparison(createComparison(candidateReports, weights, resultPreference, currentCommander, currentBaseline))}>現在の優先度で保存</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, resultPreference, currentCommander, currentBaseline), "html")}>HTMLレポートをダウンロード</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, resultPreference, currentCommander, currentBaseline), "presentation")}>プレゼンHTML</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, resultPreference, currentCommander, currentBaseline), "md")}>Markdown</button>
            <button onClick={() => downloadComparison(createComparison(candidateReports, weights, resultPreference, currentCommander, currentBaseline), "json")}>JSON</button>
          </div>
          <details><summary>都市選びの提案レポートをプレビュー（保存時点）</summary><iframe className="report-frame" title="都市選びの提案レポート" srcDoc={finished.html} sandbox="" /></details>
        </>}
      </section>
      </>}

      <footer className="app-footer">
        <span>
          {candidateReports.some((candidate) => candidateModeLabel(candidate) === "デモ表示")
            ? "開発用UI / mock data visible"
            : "出典・基準時点・利用条件を表示"}
        </span>
        <span>OpenWebUIの会話履歴は使用していません</span>
      </footer>
    </main>
  );
}

export default App;
