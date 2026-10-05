import { describe, expect, it } from "vitest";
import { createDemoReport } from "../demoReport";
import { cityHighlights } from "./highlights";
import { createComparison } from "./archive";
import type { CandidateComparison, CandidateReport } from "../types";

const weights = { convenience: 40, housing: 10, family: 30, safety: 10, future: 10 };

function commanderChoice(candidate: CandidateReport): CandidateComparison {
  return {
    recommended_region_code: candidate.report.plan.region.municipality_code,
    shared_axes: ["convenience"],
    narrative: {
      summary: "今回の条件に沿った提案です。",
      reasons: ["確認済みの根拠を使いました。"],
      tradeoffs: ["未測定の情報は別途確認してください。"],
      next_checks: ["住居候補からの経路を確認する"],
      confidence: 0.6,
    },
    used_fallback: false,
  };
}

describe("city portrait", () => {
  it("uses the named city, narrative and non-mock evidence without inventing a landmark", () => {
    const report = createDemoReport("高崎市");
    const transport = report.axis_results.find((result) => result.axis === "convenience")!;
    transport.narrative.summary = "高崎駅は新幹線の結節点。広域への移動を選びやすい街です。";
    transport.metrics[0].is_mock = false;
    transport.metrics[0].value = 7;
    transport.metrics[0].unit = "駅";
    const candidate = { report, source: "api" as const, progress: [] };
    const highlights = cityHighlights(candidate, weights);
    expect(highlights[0].summary).toContain("高崎駅");
    expect(highlights[0].evidence).toContain("7 駅");
    const html = createComparison([candidate], weights, "広域へ通勤", commanderChoice(candidate)).html;
    expect(html.indexOf("この条件での提案")).toBeLessThan(html.indexOf("5つの視点の参考スコアを見る"));
    expect(html.indexOf("5つの視点の参考スコアを見る")).toBeLessThan(html.indexOf("高崎市の魅力"));
    expect(html).toContain("高崎駅は新幹線の結節点");
    expect(html).toContain("国土交通省");
  });

  it("does not present a low-score or missing-data axis as a strength", () => {
    const report = createDemoReport("候補地");
    report.axis_results.forEach((result) => { result.score = 20; });
    const candidate = { report, source: "demo" as const, progress: [] };
    expect(cityHighlights(candidate, weights)).toEqual([]);
    const html = createComparison([candidate], weights, "").html;
    expect(html).toContain("軸別の数値だけでは候補を選びません");
    expect(html).toContain("候補を2件選んでください");
  });

  it("distinguishes a source-backed place fact from a scored metric", () => {
    const report = createDemoReport("高崎市");
    const transport = report.axis_results.find((result) => result.axis === "convenience")!;
    transport.narrative.summary = "高崎駅には新幹線があり、広域移動の選択肢があります。";
    transport.metrics.push({
      ...transport.metrics[0],
      metric_code: "takasaki_shinkansen_connections",
      label: "高崎駅の新幹線接続",
      value: 2,
      unit: "路線",
      direction: "context_only",
      normalized_score: 0,
      is_mock: false,
      source: { ...transport.metrics[0].source, source_name: "JR東日本 高崎駅時刻表" },
    });
    const candidate = { report, source: "api" as const, progress: [] };
    const highlight = cityHighlights(candidate, weights)[0];
    expect(highlight.contextOnly).toBe(true);
    expect(highlight.source).toContain("JR東日本");
    expect(createComparison([candidate], weights, "", commanderChoice(candidate)).html).toContain("街の背景情報・採点対象外");
  });

  it("does not cite childcare background for a clinic-based single-person highlight", () => {
    const report = createDemoReport("流山市");
    const family = report.axis_results.find((result) => result.axis === "family")!;
    family.narrative.summary = "人口10万人あたり診療所数が通院先の選択肢を考える材料になります。";
    family.metrics[0] = { ...family.metrics[0], metric_code: "clinics_per_100k", label: "人口10万人あたり診療所数", is_mock: false };
    family.metrics[1] = { ...family.metrics[1], metric_code: "nursery_per_1000_children", direction: "context_only", label: "子ども千人あたり保育施設数", is_mock: false };
    const candidate = { report, source: "api" as const, progress: [] };
    expect(cityHighlights(candidate, weights).find((item) => item.axis === "family")?.evidence).toContain("診療所数");
  });
});
