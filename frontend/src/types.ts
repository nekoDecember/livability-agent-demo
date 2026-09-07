export type AxisKey = "convenience" | "housing" | "family" | "safety" | "future";

export const AXIS_ORDER: AxisKey[] = [
  "convenience",
  "housing",
  "family",
  "safety",
  "future",
];

export type ExecutionStatus = "completed" | "fallback" | "failed" | "skipped";

export interface SourceReference {
  source_id: string;
  source_name: string;
  endpoint: string;
  url: string;
  reference_date: string;
  commercial_use_note: string;
}

export interface MetricEvidence {
  metric_code: string;
  label: string;
  value: number;
  unit: string;
  normalized_score: number;
  direction: "higher_is_better" | "lower_is_better" | "context_only";
  weight: number;
  source: SourceReference;
  sample_count?: number | null;
  quality: number;
  note?: string | null;
  is_mock: boolean;
}

export interface ApiCallTrace {
  tool_name: string;
  endpoint: string;
  source_id: string;
  status: "mocked" | "success" | "failed" | "skipped";
  elapsed_ms: number;
}

export interface AxisNarrative {
  axis: AxisKey;
  summary: string;
  strengths: string[];
  cautions: string[];
}

export interface AxisResult {
  axis: AxisKey;
  label: string;
  score: number;
  confidence: number;
  narrative: AxisNarrative;
  metrics: MetricEvidence[];
  api_calls: ApiCallTrace[];
  elapsed_ms: number;
}

export interface RegionInfo {
  query: string;
  name: string;
  municipality_code: string;
  prefecture?: string | null;
  comparison_group: string;
  is_mock_resolution: boolean;
}

export interface AssessmentPlan {
  user_request: string;
  region: RegionInfo;
  weights: Partial<Record<AxisKey, number>>;
  enabled_axes: AxisKey[];
  excluded_axes: AxisKey[];
  agent_selection_reason: string;
  preferences: string[];
  weight_reason: string;
  data_mode: "mock" | "government_api";
}

export interface ExecutionStep {
  name: string;
  status: ExecutionStatus;
  elapsed_ms: number;
  detail: string;
}

export interface AssessmentReport {
  report_id: string;
  generated_at: string;
  plan: AssessmentPlan;
  overall_score: number;
  overall_confidence: number;
  axis_results: AxisResult[];
  narrative: {
    executive_summary: string;
    strengths: string[];
    cautions: string[];
    suggested_followups: string[];
  };
  execution_steps: ExecutionStep[];
  total_elapsed_ms: number;
  artifacts: {
    markdown_path: string;
    json_path: string;
  };
  disclaimers: string[];
}

export interface AssessmentResponse {
  report: AssessmentReport;
  markdown: string;
}

export interface CandidateReport {
  report: AssessmentReport;
  source: "demo" | "api";
  progress: string[];
}

export interface AssessmentRequest {
  request: string;
  enabled_axes?: AxisKey[];
}

export interface RunProgress {
  candidate: string;
  message: string;
}
