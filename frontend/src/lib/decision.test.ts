import { describe, expect, it } from "vitest";
import { createDemoReport } from "../demoReport";
import type { AxisKey, CandidateReport } from "../types";
import { buildEvidenceSummary, comparisonAxisLabel } from "./decision";

const equalWeights: Record<AxisKey, number> = { convenience: 20, housing: 20, family: 20, safety: 20, future: 20 };
const candidate = (name: string, code: string): CandidateReport => {
  const report = createDemoReport(name);
  report.plan.region.municipality_code = code;
  return { report, source: "api", progress: [] };
};

describe("commander evidence summary", () => {
  it("shows axis evidence and priority shares without selecting a city by a sum", () => {
    const first = candidate("高崎市", "10202");
    const second = candidate("前橋市", "10201");
    first.report.axis_results.find((item) => item.axis === "convenience")!.score = 90;
    second.report.axis_results.find((item) => item.axis === "convenience")!.score = 50;
    const mobility = buildEvidenceSummary(
      [first, second],
      { ...equalWeights, convenience: 80, family: 5 },
      "車なし・一人暮らし",
    );
    const family = buildEvidenceSummary(
      [first, second],
      { ...equalWeights, convenience: 5, family: 80 },
      "子育て世帯",
    );

    expect(mobility.axisVerdicts.find((item) => item.axis === "convenience")?.leaders[0].report.plan.region.name).toBe("高崎市");
    expect(mobility.axisVerdicts.find((item) => item.axis === "convenience")?.importance).toBeGreaterThan(
      family.axisVerdicts.find((item) => item.axis === "convenience")!.importance,
    );
    expect(mobility).not.toHaveProperty("choice");
    expect(mobility).not.toHaveProperty("scores");
    expect(mobility.reason).toContain("コマンダーが条件と所見を合わせて判断");
  });

  it("turns written conditions into useful next checks without inventing measurements", () => {
    const candidates = [candidate("高崎市", "10202"), candidate("前橋市", "10201")];
    expect(buildEvidenceSummary(candidates, equalWeights, "車通勤・勤務地:高崎駅周辺").nextCheck).toContain("経路・所要時間");
    expect(buildEvidenceSummary(candidates, equalWeights, "子育て世帯").nextCheck).toContain("保育施設の空き状況");
    expect(buildEvidenceSummary(candidates, equalWeights, "家賃を抑えたい").nextCheck).toContain("募集中物件");
  });

  it("does not summarize a winner when comparable indicators are absent", () => {
    const first = candidate("流山市", "12220");
    const second = candidate("柏市", "12217");
    first.report.axis_results = first.report.axis_results.filter((item) => item.axis === "housing");
    second.report.axis_results = second.report.axis_results.filter((item) => item.axis === "future");
    const evidence = buildEvidenceSummary([first, second], equalWeights, "車通勤・勤務地:流山駅");
    expect(evidence.sharedAxes).toHaveLength(0);
    expect(evidence.axisVerdicts).toHaveLength(0);
    expect(evidence.reason).toContain("推薦しません");
  });

  it("keeps a close axis comparison as a near tie", () => {
    const first = candidate("流山市", "12220");
    const second = candidate("柏市", "12217");
    second.report.axis_results.find((item) => item.axis === "convenience")!.score = 75.7;
    const evidence = buildEvidenceSummary([first, second], equalWeights, "");
    expect(evidence.axisVerdicts.find((item) => item.axis === "convenience")?.nearTie).toBe(true);
    expect(evidence.axisVerdicts.find((item) => item.axis === "convenience")?.leaders).toHaveLength(2);
  });

  it("labels transport as unmeasured when only retail and city activity indicators exist", () => {
    const first = candidate("高崎市", "10202");
    const second = candidate("前橋市", "10201");
    for (const item of [first, second]) {
      item.report.axis_results.find((axis) => axis.axis === "convenience")!.metrics = item.report.axis_results.find((axis) => axis.axis === "convenience")!.metrics.map((metric) => ({ ...metric, metric_code: "retail_density", label: "小売・サービス事業所密度" }));
    }
    expect(comparisonAxisLabel([first, second], "convenience")).toBe("買い物・都市活動（交通未評価）");
  });

  it("does not call housing stock a cost or clinic access childcare", () => {
    const first = candidate("高崎市", "10202");
    const second = candidate("前橋市", "10201");
    for (const item of [first, second]) {
      item.report.axis_results.find((axis) => axis.axis === "housing")!.metrics = [{
        ...item.report.axis_results.find((axis) => axis.axis === "housing")!.metrics[0],
        metric_code: "housing_stock", label: "世帯あたり住宅ストック",
      }];
      item.report.axis_results.find((axis) => axis.axis === "family")!.metrics = [{
        ...item.report.axis_results.find((axis) => axis.axis === "family")!.metrics[0],
        metric_code: "clinics_per_100k", label: "人口10万人あたり診療所数",
      }];
    }
    expect(comparisonAxisLabel([first, second], "housing")).toBe("住宅ストック（費用未評価）");
    expect(comparisonAxisLabel([first, second], "family")).toBe("医療（子育て未評価）");
  });

  it("shows future population indicators as their own evidence label", () => {
    const first = candidate("高崎市", "10202");
    const second = candidate("前橋市", "10201");
    expect(comparisonAxisLabel([first, second], "future")).toBe("将来人口の見通し");
  });
});
