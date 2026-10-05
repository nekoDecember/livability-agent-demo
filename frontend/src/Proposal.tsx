import { useEffect, useRef, useState } from "react";
import { baselineIsOffline, candidatePosition, comparisonChange, proposalEvidence, proposalSlides, type ProposalInput } from "./lib/proposal";

export function SalesProposal({ input, comparing, error, onCompare }: { input: ProposalInput; comparing: boolean; error: string; onCompare: () => void }) {
  const evidence = proposalEvidence(input.candidates, input.weights, input.commander).slice(0, 6);
  return <>
    <section className="sales-section" aria-labelledby="sales-evidence-title">
      <span className="section-index">PROPOSAL / EVIDENCE</span>
      <h2 id="sales-evidence-title">暮らしに関わる、確かめられた違い</h2>
      <p>あなたの希望を、住まい選びの手掛かりに。街全体の数字から分かったことと、物件選びで確かめることを紹介します。</p>
      {evidence.length ? <div className="sales-grid">{evidence.map(item => <article className="sales-card" key={item.code}>
        <span className="sales-label">{item.decisive ? "おすすめの理由に使った根拠" : "街を選ぶ手掛かり"}</span>
        <h3>{item.label}</h3><strong>{item.verdict}</strong><p>{item.meaning}</p>
        <p className="sales-next-check"><b>物件選びでは</b><br />{item.nextCheck}</p>
        <details className="sales-numbers"><summary>数字・出典を見る</summary><p>{item.metricLabel}<br />{item.comparison}</p>{item.difference && <small>{item.difference}</small>}<small>{item.source}</small>{item.urls.map((url, index) => <a href={url} target="_blank" rel="noreferrer" key={url}>出典{item.urls.length > 1 ? index + 1 : ""} ↗</a>)}</details>
      </article>)}</div> : <p className="sales-empty">今回の結果には比較可能な出典付きの数値根拠がありません。提案は調査前の仮説としてご覧ください。</p>}
    </section>
    <section className="sales-section" aria-labelledby="sales-alternatives-title">
      <span className="section-index">PROPOSAL / ALTERNATIVES</span><h2 id="sales-alternatives-title">各都市を選ぶなら、どんな条件か</h2>
      <div className="sales-grid">{input.candidates.map(candidate => {
        const code = candidate.report.plan.region.municipality_code;
        const position = candidatePosition(input, candidate);
        return <article className={`sales-card ${input.commander?.recommended_region_code === code ? "sales-selected" : ""}`} key={code}>
          <span className="sales-label">{input.commander?.recommended_region_code === code ? "今回の第一候補" : "比較候補"}</span>
          <h3>{candidate.report.plan.region.name}</h3><p>{position.fit}</p><strong>選ぶ条件</strong><p>{position.condition}</p>
        </article>;
      })}</div>
    </section>
    {!!evidence.length && <section className="sales-section" aria-labelledby="sales-comparison-title">
      <span className="section-index">BEFORE / AFTER</span><h2 id="sales-comparison-title">街の印象から、確かめた提案へ</h2>
      <p>同じ都市と希望について、AIの一般知識で考えた提案と、地域のデータを確かめた提案を比べます。おすすめが同じでも、その理由を数字で確かめられたかが分かります。</p>
      <details className="comparison-method"><summary>比較の条件を見る</summary><p>どちらも同じコマンダーで結論をまとめます。調査段階の構成は異なるため、データだけの効果を測る厳密な実験ではありません。</p></details>
      <button type="button" disabled={comparing} onClick={onCompare}>{comparing ? "同じ条件で比較を作成中…" : input.baseline ? "比較を再作成" : "データなしの提案と比較する"}</button>
      {error && <p role="alert">{error}</p>}
      {input.baseline && <>
        <p className="sales-change">{comparisonChange(input)}</p>
        <div className="sales-grid before-after">
          <article className="sales-card"><span className="sales-label">調べる前 / {baselineIsOffline(input.baseline) ? "代替処理を含む" : "AIの一般知識"}</span><h3>{input.baseline.commander.narrative.summary}</h3><ul>{input.baseline.commander.narrative.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul><strong>次に確認</strong><ul>{input.baseline.commander.narrative.next_checks.map((check, index) => <li key={index}>{check}</li>)}</ul><small>今回、地域データや検索による裏付けは確認していません。</small></article>
          <article className="sales-card sales-selected"><span className="sales-label">確かめた後 / 地域データを使用</span><h3>{input.commander?.narrative.summary}</h3><ul>{input.commander?.narrative.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul><strong>数字で確かめられたこと</strong><ul>{evidence.slice(0, 3).map(item => <li key={item.code}>{item.verdict}</li>)}</ul><strong>次に確認</strong><ul>{input.commander?.narrative.next_checks.map((check, index) => <li key={index}>{check}</li>)}</ul><small>根拠の数字と出典は、上の「数字・出典を見る」で確認できます。</small></article>
        </div>
      </>}
    </section>}
  </>;
}

export function ProposalPresentation({ input, onClose }: { input: ProposalInput; onClose: () => void }) {
  const slides = proposalSlides(input);
  const [index, setIndex] = useState(0);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowRight") setIndex(value => Math.min(slides.length - 1, value + 1));
      if (event.key === "ArrowLeft") setIndex(value => Math.max(0, value - 1));
    };
    document.addEventListener("keydown", keydown);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", keydown); document.body.style.overflow = overflow; previous?.focus(); };
  }, [onClose, slides.length]);
  const slide = slides[index];
  return <div className="proposal-presentation" role="dialog" aria-modal="true" aria-label="都市選びの提案プレゼン" onKeyDown={event => {
    if (event.key !== "Tab") return;
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
    if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1)?.focus(); }
    if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0]?.focus(); }
  }}>
    <div className="presentation-top"><span>LIVABILITY / あなたの都市選びをご提案</span><button ref={close} type="button" onClick={onClose}>閉じる（Esc）</button></div>
    <article className="presentation-slide" aria-live="polite"><span className="section-index">PROPOSAL {String(index + 1).padStart(2, "0")}</span><h2>{slide.title}</h2><p className="presentation-lead">{slide.lead}</p><ul>{slide.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul></article>
    <nav className="presentation-nav" aria-label="プレゼンのページ"><button type="button" disabled={index === 0} onClick={() => setIndex(index - 1)}>← 前へ</button><span>{index + 1} / {slides.length}</span><button type="button" disabled={index === slides.length - 1} onClick={() => setIndex(index + 1)}>次へ →</button></nav>
  </div>;
}
