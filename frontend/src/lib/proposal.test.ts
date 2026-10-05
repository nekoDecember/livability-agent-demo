import { describe, expect, it } from "vitest";
import { createDemoReport } from "../demoReport";
import type { CandidateComparison, CandidateReport } from "../types";
import { createComparison, readArchive } from "./archive";
import { comparisonChange, proposalEvidence, proposalSlides, proposalStrength } from "./proposal";
import { renderPresentationHtml } from "./presentationHtml";
import { customerEvidence } from "./customerEvidence";

const weights = { convenience: 20, housing: 20, family: 20, safety: 20, future: 20 };
function measured(name: string): CandidateReport {
  const report = createDemoReport(name);
  report.plan.data_mode = "open_data";
  report.axis_results = report.axis_results.filter(result => result.axis === "housing");
  report.axis_results[0].metrics = [{ ...report.axis_results[0].metrics[0], metric_code: "residential_land_price", label: "住宅地基準地点価格", unit: "円/㎡", value: name === "流山市" ? 50000 : 60000, quality: 1, direction: "lower_is_better", is_mock: false }];
  return { report, source: "api", progress: [] };
}
const commander: CandidateComparison = {
  recommended_region_code: "12220", shared_axes: ["housing"], used_fallback: false,
  narrative: { summary: "土地購入を重視するなら流山市を第一候補にします。", reasons: ["住宅地価格の比較を決め手にしました。"], tradeoffs: ["通勤時間は別途確認します。"], next_checks: ["同条件の物件を比較する"], confidence: .7, supporting_metric_codes: ["residential_land_price"], recommendation_strength: "recommended" },
};

describe("sales proposal evidence and exports", () => {
  it("compares raw measured values and links the commander's decisive evidence", () => {
    const evidence = proposalEvidence([measured("流山市"), measured("柏市")], weights, commander);
    expect(evidence).toHaveLength(1);
    expect(evidence[0].label).toBe("家を建てるための土地予算");
    expect(evidence[0].verdict).toContain("流山市は、住宅地の基準地点の価格が低い");
    expect(evidence[0].difference).toContain("20%");
    expect(evidence[0].comparison).toContain("50000円/㎡");
    expect(evidence[0].decisive).toBe(true);
    expect(proposalStrength([measured("流山市"), measured("柏市")], commander)).toBe("根拠に基づく推薦");
  });
  it.each(["missing", "year", "unit", "direction", "source", "mock", "knowledge", "context"])("does not present %s evidence as a verified comparison", reason => {
    const candidates = [measured("流山市"), measured("柏市")];
    const metric = candidates[1].report.axis_results[0].metrics[0];
    if (reason === "missing") metric.quality = 0;
    if (reason === "year") metric.source = { ...metric.source, reference_date: "別年" };
    if (reason === "unit") metric.unit = "円/坪";
    if (reason === "direction") metric.direction = "higher_is_better";
    if (reason === "source") metric.source = { ...metric.source, source_id: "other" };
    if (reason === "mock") metric.is_mock = true;
    if (reason === "knowledge") candidates[1].report.plan.data_mode = "knowledge_only";
    if (reason === "context") metric.direction = "context_only";
    expect(proposalEvidence(candidates, weights, commander)).toEqual([]);
  });
  it("treats measured zero as a fact and reports an exact tie without inventing superiority", () => {
    const candidates = [measured("流山市"), measured("柏市")];
    candidates.forEach(candidate => candidate.report.axis_results[0].metrics[0].value = 0);
    const evidence = proposalEvidence(candidates, weights, commander);
    expect(evidence[0].verdict).toContain("ここでは候補を選び分けません");
    expect(evidence[0].comparison).toContain("0円/㎡");
  });
  it("preserves both actual runs in the archive, report and presentation", () => {
    const candidates = [measured("流山市"), measured("柏市")];
    const baseline = { candidates: candidates.map(candidate => ({ ...candidate, source: "knowledge" as const, report: { ...candidate.report, plan: { ...candidate.report.plan, data_mode: "knowledge_only" as const } } })), commander: { ...commander, recommended_region_code: "12217", narrative: { ...commander.narrative, summary: "調査前は柏市を提案します。" } } };
    const saved = createComparison(candidates, weights, "土地購入", commander, baseline);
    expect(comparisonChange(saved)).toContain("柏市 → 流山市");
    expect(saved.html).toContain("調査前は柏市を提案します。");
    expect(saved.html).toContain("土地分の予算を比べる手掛かり");
    expect(saved.html).toContain("数字・出典を見る");
    expect(saved.html).toContain("50000円/㎡");
    expect(saved.markdown).toContain("売地の総額と建築費を確認");
    expect(saved.markdown).toContain("データなし／ありの比較");
    expect(readArchive({ getItem: () => JSON.stringify([saved]) })[0].baseline).toEqual(baseline);
    expect(proposalSlides(saved)).toHaveLength(6);
    expect(renderPresentationHtml(saved)).toContain("50000円/㎡");
    expect(renderPresentationHtml(saved)).toContain("調査前は柏市を提案します。");
    const broken = { ...saved, baseline: { ...baseline, candidates: [] } };
    expect(readArchive({ getItem: () => JSON.stringify([broken]) })).toEqual([]);
  });
  it("keeps everyday interpretations within the scope of each statistic", () => {
    const clinic = customerEvidence("clinics_per_100k", "診療所", "柏市", false, false);
    expect(clinic.verdict).toBe("柏市は、人口あたりの診療所が多い");
    expect(clinic.meaning).not.toMatch(/近い|待ち時間|必ず/);
    expect(clinic.nextCheck).toContain("診療科");
    const stock = customerEvidence("housing_stock", "住宅数", "柏市", false, false);
    expect(stock.meaning + stock.verdict).not.toMatch(/安い|空室|募集中/);
    expect(stock.nextCheck).toContain("空室や安さは分かりません");
    const nursery = customerEvidence("nursery_per_1000_children", "保育", "柏市", false, false);
    expect(nursery.meaning + nursery.verdict).not.toMatch(/入りやすい|入園できる/);
    expect(nursery.nextCheck).toContain("空き状況");
    const future = customerEvidence("population_retention_2050", "人口", "柏市", false, false);
    expect(future.verdict).toContain("推計");
    expect(future.meaning).not.toMatch(/資産価値|保証/);
    expect(customerEvidence("unknown", "未登録指標", "柏市", false, true).meaning).toContain("個別に確認");
  });
  it("presents the commander's main reason before a higher weighted background comparison", () => {
    const candidates = [measured("流山市"), measured("柏市")];
    candidates.forEach((candidate, index) => {
      const housing = candidate.report.axis_results[0];
      candidate.report.axis_results.push({ ...housing, axis: "family", metrics: [{ ...housing.metrics[0], metric_code: "clinics_per_100k", label: "診療所", value: 90 + index * 10, unit: "施設/10万人", direction: "higher_is_better" }] });
    });
    const advice = { ...commander, narrative: { ...commander.narrative, supporting_metric_codes: ["clinics_per_100k", "residential_land_price"] } };
    const evidence = proposalEvidence(candidates, { ...weights, housing: 80, family: 10 }, advice);
    expect(evidence.map(item => item.code)).toEqual(["clinics_per_100k", "residential_land_price"]);
  });
  it("keeps a matching recommendation as a matching recommendation, and escapes presentation text", () => {
    const candidates = [measured("流山市"), measured("柏市")];
    const input = { candidates, commander, baseline: { candidates, commander }, weights, preference: "<script>alert(1)</script>" };
    expect(comparisonChange(input)).toContain("一致");
    expect(renderPresentationHtml(input)).not.toContain("<script>");
    expect(renderPresentationHtml(input)).toContain("&lt;script&gt;");
  });
});
