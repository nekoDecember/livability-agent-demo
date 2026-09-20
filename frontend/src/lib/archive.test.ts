import { describe, expect, it } from "vitest";
import { createDemoReport } from "../demoReport";
import { ARCHIVE_KEY, createComparison, readArchive } from "./archive";
const weights = { convenience: 20, housing: 20, family: 20, safety: 20, future: 20 };
describe("comparison archive", () => {
  it("exports source reports, weights, evidence and demo limitations in one restorable snapshot", () => {
    const candidate = { report: createDemoReport("流山市"), source: "demo" as const, progress: [], markdown: "元レポート本文" };
    const saved = createComparison([candidate], weights, "車なし");
    expect(saved.markdown).toContain("## 結論");
    expect(saved.markdown).toContain("## 比較表");
    expect(saved.markdown).toContain("## 評価軸");
    expect(saved.markdown).toContain("### 根拠・出典");
    expect(saved.markdown).toContain("意思決定には使用できません");
    expect(saved.markdown).toContain("元レポート本文");
    expect(saved.markdown).toMatch(/\| 候補地.+\n\|---.+\n\| 流山市/);
    expect(saved.html).toContain("住みやすさ比較レポート");
    expect(saved.html).toContain("流山市を第一候補にします");
    expect(saved.html).toContain("<table>");
    const restored = readArchive({ getItem: key => key === ARCHIVE_KEY ? JSON.stringify([saved]) : null });
    expect(restored).toEqual([saved]);
    weights.housing = 40;
    expect(saved.weights.housing).toBe(20);
    weights.housing = 20;
  });
  it("rejects broken stored reports without breaking the viewer", () => {
    expect(readArchive({ getItem: () => JSON.stringify([{ id: "broken", candidates: [null] }]) })).toEqual([]);
    expect(() => readArchive({ getItem: () => "{" })).toThrow();
  });
});
