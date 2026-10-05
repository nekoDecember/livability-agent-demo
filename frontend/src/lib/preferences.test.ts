import { describe, expect, it } from "vitest";
import { prioritizeAxis, suggestedWeights } from "./preferences";

describe("living condition weights", () => {
  it("raises travel and family priorities for a car-free commuting family", () => {
    const { weights, reasons } = suggestedWeights("車なし・子育て・都内へ週3通勤");
    expect(weights.convenience).toBeGreaterThan(20);
    expect(weights.family).toBeGreaterThan(weights.housing);
    expect(weights.family).toBeGreaterThan(weights.convenience);
    expect(reasons).not.toContain("鉄道・公共交通");
    expect(Object.values(weights).reduce((a, b) => a + b)).toBeCloseTo(100);
  });

  it("does not prioritize rail data for a car commute", () => {
    const { weights, reasons } = suggestedWeights("高崎駅周辺勤務・独身25歳・車通勤");
    expect(weights.convenience).toBeLessThan(weights.housing);
    expect(weights.housing).toBeGreaterThan(20);
    expect(reasons).toContain("単身の住まい");
    expect(reasons).not.toContain("鉄道・公共交通");
  });

  it("does not add weight for an unmeasured car commute", () => {
    const { weights, reasons } = suggestedWeights("車通勤・勤務地:高崎駅周辺");
    expect(weights).toEqual({
      convenience: 20, housing: 20, family: 20, safety: 20, future: 20,
    });
    expect(reasons).toHaveLength(0);
  });

  it("changes the profile when household and age conditions change", () => {
    const single = suggestedWeights("25歳・独身");
    const parent = suggestedWeights("35歳・子育て世帯");
    const older = suggestedWeights("70歳・夫婦");
    expect(single.weights.housing).toBeGreaterThan(single.weights.family);
    expect(parent.weights.family).toBeGreaterThan(parent.weights.housing);
    expect(older.weights.family).toBeGreaterThan(older.weights.convenience);
    expect(new Set([single.weights.family, parent.weights.family, older.weights.family]).size).toBe(3);
  });

  it("prioritizes rail data for an explicit Shinkansen commute", () => {
    const { weights, reasons } = suggestedWeights("新幹線通勤");
    expect(weights.convenience).toBeGreaterThan(20);
    expect(reasons).toContain("鉄道・公共交通");
  });

  it("uses equal weights when no condition is recognized", () => {
    expect(suggestedWeights("").weights).toEqual({
      convenience: 20, housing: 20, family: 20, safety: 20, future: 20,
    });
  });

  it("lets the user state one decisive axis explicitly", () => {
    const weights = prioritizeAxis(suggestedWeights("").weights, "future");
    expect(weights.future).toBe(50);
    expect(weights.convenience).toBe(12.5);
  });
});
