import { describe, expect, it } from "vitest";
import { createDemoReport } from "../demoReport";
import { ARCHIVE_KEY, createComparison, readArchive } from "./archive";
import type { CandidateComparison } from "../types";
const weights = { convenience: 20, housing: 20, family: 20, safety: 20, future: 20 };
describe("comparison archive", () => {
  it("exports source reports, weights, evidence and demo limitations in one restorable snapshot", () => {
    const candidate = { report: createDemoReport("流山市"), source: "demo" as const, progress: [], markdown: "元レポート本文" };
    const saved = createComparison([candidate], weights, "車なし");
    expect(saved.markdown).toContain("## 結論");
    expect(saved.markdown).toContain("## 専門Agentの調査根拠（補足）");
    expect(saved.markdown).not.toContain("優勢な候補");
    expect(saved.markdown).toContain("### 根拠・出典");
    expect(saved.markdown).toContain("意思決定には使用できません");
    expect(saved.markdown).toContain("司令塔の統合提案はまだ作成されていません");
    expect(saved.markdown).not.toContain("第一候補として検討");
    expect(saved.candidates[0].markdown).toBe("元レポート本文");
    expect(saved.markdown).not.toContain("元レポート本文");
    expect(saved.markdown).toContain("| 視点 | 司令塔へ伝えた優先度 |");
    expect(saved.html).toContain("都市選びの提案レポート");
    expect(saved.html).toContain("候補を2件選んでください");
    expect(saved.html).toContain("候補ごとの調査メモ");
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

  it("keeps older saved reports and maps their confidence to evidence confidence", () => {
    const candidate = { report: createDemoReport("流山市"), source: "demo" as const, progress: [] };
    const saved = createComparison([candidate], weights, "車なし");
    const legacy = JSON.parse(JSON.stringify(saved));
    legacy.candidates[0].report.overall_confidence = legacy.candidates[0].report.research_confidence;
    delete legacy.candidates[0].report.research_confidence;

    const restored = readArchive({ getItem: key => key === ARCHIVE_KEY ? JSON.stringify([legacy]) : null });
    expect(restored).toHaveLength(1);
    expect(restored[0].candidates[0].report.research_confidence).toBe(0.5);
  });

  it("does not invent a rank when no indicators are comparable", () => {
    const first = { report: createDemoReport("流山市"), source: "demo" as const, progress: [] };
    const second = { report: createDemoReport("柏市"), source: "demo" as const, progress: [] };
    first.report.axis_results = first.report.axis_results.filter((axis) => axis.axis === "convenience");
    second.report.axis_results = second.report.axis_results.filter((axis) => axis.axis === "housing");
    const saved = createComparison([first, second], weights, "指定なし");
    expect(saved.markdown).toContain("共通指標なし");
    expect(saved.markdown).not.toContain("優勢な候補");
    expect(saved.html).toContain("司令塔の統合結果がありません");
    expect(saved.html).not.toContain("今回の選択");
  });

  it("uses the commander's qualitative choice rather than the weighted axis order in the export", () => {
    const first = { report: createDemoReport("流山市"), source: "demo" as const, progress: [] };
    const second = { report: createDemoReport("柏市"), source: "demo" as const, progress: [] };
    const commander: CandidateComparison = {
      recommended_region_code: "12217",
      shared_axes: ["convenience", "housing"],
      narrative: {
        summary: "通勤条件の未確認を踏まえ、柏市の物件から具体的に確認する提案です。",
        reasons: ["条件に関連する根拠です。"],
        tradeoffs: ["流山市には別の強みがあります。"],
        next_checks: ["勤務地までの実ルートを確認する"],
        confidence: 0.6,
      },
      used_fallback: false,
    };

    const saved = createComparison([first, second], weights, "通勤", commander);

    expect(saved.markdown).toContain("通勤条件の未確認を踏まえ");
    expect(saved.html).toContain("調査前の仮説：柏市を第一候補に");
    expect(saved.html).toContain("司令塔の提案候補");
    expect(saved.html).not.toContain("この軸で優勢");
    expect(saved.html).toContain("5つの視点の参考スコアを見る");
  });
});
