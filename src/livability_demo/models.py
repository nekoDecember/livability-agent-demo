from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class Axis(StrEnum):
    CONVENIENCE = "convenience"
    HOUSING = "housing"
    FAMILY = "family"
    SAFETY = "safety"
    FUTURE = "future"


AXIS_LABELS: dict[Axis, str] = {
    Axis.CONVENIENCE: "移動・買い物",
    Axis.HOUSING: "住まいコスト",
    Axis.FAMILY: "医療・子育て",
    Axis.SAFETY: "安心・防災",
    Axis.FUTURE: "まちの将来性",
}


class RegionInfo(BaseModel):
    query: str
    name: str
    municipality_code: str
    prefecture: str | None = None
    comparison_group: str = "同程度の人口規模の市区町村"
    is_mock_resolution: bool = False


class SourceReference(BaseModel):
    source_id: str
    source_name: str
    endpoint: str
    url: str
    reference_date: str
    commercial_use_note: str


class MetricEvidence(BaseModel):
    metric_code: str
    label: str
    value: float
    unit: str
    normalized_score: float = Field(ge=0, le=100)
    direction: Literal["higher_is_better", "lower_is_better", "context_only"]
    weight: float = Field(gt=0)
    source: SourceReference
    sample_count: int | None = None
    quality: float = Field(default=1.0, ge=0, le=1)
    note: str | None = None
    is_mock: bool = False


class ApiCallTrace(BaseModel):
    tool_name: str
    endpoint: str
    source_id: str
    status: Literal["mocked", "success", "failed", "skipped"]
    elapsed_ms: int


class AxisEvidence(BaseModel):
    axis: Axis
    region: RegionInfo
    metrics: list[MetricEvidence]
    api_calls: list[ApiCallTrace]
    elapsed_ms: int
    data_mode: Literal["mock", "government_api"]


class AxisNarrative(BaseModel):
    axis: Axis
    summary: str
    strengths: list[str] = Field(min_length=1, max_length=3)
    cautions: list[str] = Field(min_length=1, max_length=3)


class AssessmentPlan(BaseModel):
    user_request: str
    region: RegionInfo
    weights: dict[Axis, float]
    enabled_axes: list[Axis] = Field(min_length=1)
    excluded_axes: list[Axis]
    agent_selection_reason: str
    preferences: list[str]
    weight_reason: str
    data_mode: Literal["mock", "government_api"]

    @model_validator(mode="after")
    def validate_agent_selection(self) -> Self:
        enabled = set(self.enabled_axes)
        excluded = set(self.excluded_axes)
        if len(enabled) != len(self.enabled_axes) or len(excluded) != len(self.excluded_axes):
            raise ValueError("Agent selection cannot contain duplicate axes.")
        if enabled & excluded:
            raise ValueError("Enabled and excluded axes must be disjoint.")
        if enabled | excluded != set(Axis):
            raise ValueError("Every axis must be either enabled or excluded.")
        if set(self.weights) != enabled:
            raise ValueError("Weights must be defined for enabled axes only.")
        if abs(sum(self.weights.values()) - 100) > 0.11:
            raise ValueError("Enabled-axis weights must sum to 100.")
        return self


class AxisResult(BaseModel):
    axis: Axis
    label: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    narrative: AxisNarrative
    metrics: list[MetricEvidence]
    api_calls: list[ApiCallTrace]
    elapsed_ms: int


class FinalNarrative(BaseModel):
    executive_summary: str
    strengths: list[str] = Field(min_length=1, max_length=3)
    cautions: list[str] = Field(min_length=1, max_length=3)
    suggested_followups: list[str] = Field(min_length=1, max_length=3)


class ExecutionStep(BaseModel):
    name: str
    status: Literal["completed", "fallback", "failed", "skipped"]
    elapsed_ms: int
    detail: str


class ReportArtifacts(BaseModel):
    markdown_path: str = ""
    json_path: str = ""


class AssessmentReport(BaseModel):
    report_id: str
    generated_at: datetime
    plan: AssessmentPlan
    overall_score: float = Field(ge=0, le=100)
    overall_confidence: float = Field(ge=0, le=1)
    axis_results: list[AxisResult]
    narrative: FinalNarrative
    execution_steps: list[ExecutionStep]
    total_elapsed_ms: int
    artifacts: ReportArtifacts = Field(default_factory=ReportArtifacts)
    disclaimers: list[str]
