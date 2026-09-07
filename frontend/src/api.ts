import type {
  AssessmentRequest,
  AssessmentResponse,
  RunProgress,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

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
