export type AxisKey = "convenience" | "housing" | "family" | "safety" | "future";

export const AXIS_ORDER: AxisKey[] = [
  "convenience",
  "housing",
  "family",
  "safety",
  "future",
];

export type ExecutionStatus = "completed" | "fallback" | "failed" | "skipped";
export type AssessmentMode = "data" | "knowledge_only";

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
  missing_reason?: "not_collected" | "not_found_for_region" | "unverified" | null;
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
  unavailable_axes?: AxisKey[];
  unavailable_axis_reasons?: Partial<Record<AxisKey, "not_collected" | "not_found_for_region" | "unverified">>;
  agent_selection_reason: string;
  preferences: string[];
  weight_reason: string;
  data_mode: "mock" | "open_data" | "government_api" | "knowledge_only";
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
  research_confidence: number;
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

export interface CommanderNarrative {
  summary: string;
  reasons: string[];
  tradeoffs: string[];
  next_checks: string[];
  confidence: number;
  proposal_title?: string;
  recommendation_strength?: "recommended" | "conditional" | "hypothesis" | "undecided";
  supporting_metric_codes?: string[];
  candidate_positions?: { region_code: string; fit_summary: string; selection_condition: string }[];
}

export interface KnowledgeBaseline {
  candidates: CandidateReport[];
  commander: CandidateComparison;
}

export interface CandidateComparison {
  recommended_region_code: string | null;
  shared_axes: AxisKey[];
  narrative: CommanderNarrative;
  used_fallback: boolean;
}

export interface CandidateReport {
  markdown?: string;
  report: AssessmentReport;
  source: "demo" | "api" | "knowledge";
  progress: string[];
}

export interface AssessmentRequest {
  request: string;
  enabled_axes?: AxisKey[];
  weights?: Partial<Record<AxisKey, number>>;
  mode?: AssessmentMode;
}

export interface RunProgress {
  candidate: string;
  message: string;
}
