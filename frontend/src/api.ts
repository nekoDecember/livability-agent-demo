import type {
  AssessmentReport,
  AssessmentRequest,
  AssessmentResponse,
  CandidateComparison,
  RunProgress,
  AxisKey,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export interface RegionOption {
  name: string;
  prefecture: string;
  municipality_code: string;
}

export async function listRegions(): Promise<RegionOption[]> {
  const response = await fetch(`${API_BASE_URL}/v1/agent/regions`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`自治体一覧を取得できませんでした (${response.status})`);
  const body = await response.json() as { regions?: RegionOption[] };
  if (!Array.isArray(body.regions)) throw new Error("自治体一覧の形式が正しくありません");
  return body.regions;
}

interface ServerEvent {
  event: string;
  payload: Record<string, unknown>;
}

function requestHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
}

function parseEventBlock(block: string): ServerEvent | null {
  let event = "message";
  let data = "";

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    if (line.startsWith("data:")) data += line.slice("data:".length).trim();
  }

  if (!data) return null;
  return { event, payload: JSON.parse(data) as Record<string, unknown> };
}

async function consumeServerEvents(
  response: Response,
  onEvent: (event: ServerEvent) => void,
): Promise<void> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `API request failed: ${response.status}`);
  }
  if (!response.body) throw new Error("The API did not return a readable event stream.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    blocks.forEach((block) => {
      const event = parseEventBlock(block);
      if (event) onEvent(event);
    });
    if (done) break;
  }

  const finalEvent = parseEventBlock(buffer);
  if (finalEvent) onEvent(finalEvent);
}

export async function runAssessment(
  request: AssessmentRequest,
  onProgress: (progress: RunProgress) => void,
): Promise<AssessmentResponse> {
  let result: AssessmentResponse | null = null;
  let streamError: string | null = null;

  const response = await fetch(`${API_BASE_URL}/v1/agent/assessments/stream`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(request),
  });

  await consumeServerEvents(response, (event) => {
    if (event.event === "progress" && typeof event.payload.message === "string") {
      onProgress({ candidate: request.request, message: event.payload.message });
    }
    if (event.event === "result") {
      result = event.payload as unknown as AssessmentResponse;
    }
    if (event.event === "error") {
      streamError = typeof event.payload.message === "string" ? event.payload.message : "Unknown API error";
    }
  });

  if (streamError) throw new Error(streamError);
  if (!result) throw new Error("The API stream ended without a report.");
  return result;
}

export async function runCommander(
  reports: AssessmentReport[],
  request: string,
  weights: Partial<Record<AxisKey, number>>,
): Promise<CandidateComparison> {
  const response = await fetch(`${API_BASE_URL}/v1/agent/comparisons`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ reports, request, weights }),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `候補地を統合できませんでした (${response.status})`);
  }
  const body = await response.json() as { comparison?: CandidateComparison };
  if (!body.comparison || !body.comparison.narrative) {
    throw new Error("コマンダーの応答形式が正しくありません");
  }
  const regionCodes = new Set(reports.map((report) => report.plan.region.municipality_code));
  if (body.comparison.recommended_region_code && !regionCodes.has(body.comparison.recommended_region_code)) {
    throw new Error("コマンダーが候補に含まれない自治体を選びました");
  }
  return body.comparison;
}
