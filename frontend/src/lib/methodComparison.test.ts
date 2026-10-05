import { describe, expect, it } from "vitest";
import { createDemoReport } from "../demoReport";
import type { ComparisonMethodRun } from "../types";
import { citationHtml, searchRoundCount } from "./methodComparison";

describe("research provenance", () => {
  it("counts a shared search budget once across cities", () => {
    const candidates = ["流山市", "柏市"].map(name => {
      const report = createDemoReport(name);
      report.research_context = {
        method: "web_search", status: "searched", search_rounds: 3, max_search_rounds: 3,
        controlled_fields: [], limitations: [], sources: [], steps: [],
      };
      return { report, source: "web" as const, progress: [] };
    });
    const run: ComparisonMethodRun = { status: "completed", candidates };
    expect(searchRoundCount(run)).toEqual({ rounds: 3, maxRounds: 3 });
  });
  it("links the cited source and escapes untrusted text", () => {
    const html = citationHtml('<script>alert(1)</script> [2]', [
      { url: "https://www.stat.go.jp/", title: "統計局" },
      { url: "https://www.mlit.go.jp/", title: '\" onclick=\"alert(1)' },
    ]);
    expect(html).toContain('href="https://www.mlit.go.jp/"');
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('" onclick="');
  });
  it("does not turn unsafe or unknown source numbers into links", () => {
    const html = citationHtml("根拠 [1] [99]", [{ url: "javascript:alert(1)", title: "悪意ある出典" }]);
    expect(html).not.toContain("<a");
    expect(html).toContain("[99]");
  });
});
