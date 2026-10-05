import { afterEach, describe, expect, it, vi } from "vitest";
import { createDemoReport } from "./demoReport";
import { runSearchComparison } from "./api";
import type { SearchComparisonRequest } from "./types";

const request: SearchComparisonRequest = {
  request: "子育てを重視",
  regions: [
    { name: "流山市", municipality_code: "12220", prefecture: "千葉県" },
    { name: "柏市", municipality_code: "12217", prefecture: "千葉県" },
  ],
};
function result() {
  return {
    candidates: request.regions.map(region => ({ report: createDemoReport(region.name), markdown: "検索回答" })),
    comparison: { recommended_region_code: null, narrative: { summary: "追加確認が必要です" } },
  };
}
afterEach(() => vi.unstubAllGlobals());
describe("shared Web search request", () => {
  it("sends all cities in one request and preserves search progress", async () => {
    const payload = result();
    const fetch = vi.fn().mockResolvedValue(new Response(
      `event: progress\ndata: {"message":"Web検索 3/3"}\n\nevent: result\ndata: ${JSON.stringify(payload)}\n\n`,
    ));
    vi.stubGlobal("fetch", fetch);
    const progress = vi.fn();
    expect((await runSearchComparison(request, progress)).candidates).toHaveLength(2);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe("/api/v1/agent/search-comparisons/stream");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(request);
    expect(progress).toHaveBeenCalledWith({ candidate: "Web検索", message: "Web検索 3/3" });
  });
  it("reports a server error without a knowledge or demo fallback", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('event: error\ndata: {"message":"検索に失敗しました"}\n\n'));
    vi.stubGlobal("fetch", fetch);
    await expect(runSearchComparison(request, () => {})).rejects.toThrow("検索に失敗しました");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects duplicate cities in a search response", async () => {
    const payload = result();
    payload.candidates[1] = payload.candidates[0];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(`event: result\ndata: ${JSON.stringify(payload)}\n\n`)));
    await expect(runSearchComparison(request, () => {})).rejects.toThrow("依頼していない自治体");
  });
});
