import { describe, expect, it } from "vitest";
import { axisUnavailableLabel, commonComparisonAxes, metricAvailabilityLabel, normalizeWeights, railAccessUnverified, scoreTone } from "./report";
import { createDemoReport } from "../demoReport";

describe("report helpers", () => {
  it("normalizes user weights without changing their proportions", () => {
    expect(normalizeWeights({
      convenience: 2,
      housing: 1,
      family: 1,
      safety: 0,
      future: 0,
    })).toEqual({
      convenience: 50,
      housing: 25,
      family: 25,
      safety: 0,
      future: 0,
    });
  });

  it("redistributes only the displayed priority shares across enabled axes", () => {
    expect(normalizeWeights({
      convenience: 20,
      housing: 20,
      family: 20,
      safety: 20,
      future: 20,
    }, ["convenience", "housing"])).toEqual({
      convenience: 50,
      housing: 50,
      family: 0,
      safety: 0,
      future: 0,
    });
  });

  it("uses the same available axes for every city in a comparison", () => {
    const first = { report: createDemoReport("流山市"), source: "demo" as const, progress: [] };
    const second = { report: createDemoReport("柏市"), source: "demo" as const, progress: [] };
    second.report.axis_results = second.report.axis_results.filter((axis) => axis.axis !== "safety");
    second.report.plan.enabled_axes = second.report.plan.enabled_axes.filter((axis) => axis !== "safety");
    const shared = commonComparisonAxes([first, second]);
    expect(shared).not.toContain("safety");
    expect(shared).toHaveLength(4);
  });

  it("excludes an axis when cities were scored from different indicators", () => {
    const first = { report: createDemoReport("流山市"), source: "demo" as const, progress: [] };
    const second = { report: createDemoReport("柏市"), source: "demo" as const, progress: [] };
    second.report.axis_results.find((axis) => axis.axis === "housing")!.metrics[0].quality = 0;
    expect(commonComparisonAxes([first, second])).not.toContain("housing");
  });

  it("distinguishes a measured zero from missing values", () => {
    const metric = createDemoReport("流山市").axis_results[0].metrics[0];
    expect(metricAvailabilityLabel({ ...metric, value: 0 })).toBe("実測 0");
    expect(metricAvailabilityLabel({ ...metric, quality: 0, missing_reason: "not_collected" })).toBe("指標未収録");
    expect(metricAvailabilityLabel({ ...metric, quality: 0, missing_reason: "not_found_for_region" })).toBe("この自治体の値なし");
  });

  it("shows the reason an axis could not be evaluated", () => {
    const report = createDemoReport("流山市");
    report.plan.unavailable_axes = ["safety"];
    report.plan.unavailable_axis_reasons = { safety: "unverified" };
    expect(axisUnavailableLabel(report, "safety")).toBe("照合未確認");
  });

  it("does not treat generic station indicators as verified Shinkansen access", () => {
    const first = { report: createDemoReport("流山市"), source: "demo" as const, progress: [] };
    const second = { report: createDemoReport("柏市"), source: "demo" as const, progress: [] };
    expect(railAccessUnverified([first, second], "新幹線を重視")).toBe(true);
    expect(railAccessUnverified([first, second], "車通勤・勤務地:高崎駅周辺")).toBe(false);
  });

  it("keeps score tones semantic", () => {
    expect(scoreTone(80)).toBe("high");
    expect(scoreTone(60)).toBe("mid");
    expect(scoreTone(40)).toBe("low");
  });
});
