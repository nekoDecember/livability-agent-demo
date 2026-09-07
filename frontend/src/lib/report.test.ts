import { describe, expect, it } from "vitest";
import { normalizeWeights, scoreTone, weightedScore } from "./report";
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

  it("calculates a weighted score from axis results", () => {
    const report = createDemoReport("流山市");
    const score = weightedScore(report, {
      convenience: 1,
      housing: 0,
      family: 0,
      safety: 0,
      future: 0,
    });
    expect(score).toBe(75.5);
  });

  it("keeps score tones semantic", () => {
    expect(scoreTone(80)).toBe("high");
    expect(scoreTone(60)).toBe("mid");
    expect(scoreTone(40)).toBe("low");
  });
});
