from __future__ import annotations

import re
import unicodedata
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


DataMode = Literal["mock", "open_data", "government_api", "knowledge_only", "web_search"]


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
    missing_reason: Literal["not_collected", "not_found_for_region", "unverified"] | None = None
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
    data_mode: DataMode


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
    unavailable_axes: list[Axis] = Field(default_factory=list)
    unavailable_axis_reasons: dict[
        Axis, Literal["not_collected", "not_found_for_region", "unverified"]
    ] = Field(default_factory=dict)
    agent_selection_reason: str
    preferences: list[str]
    weight_reason: str
    data_mode: DataMode

    @model_validator(mode="after")
    def validate_agent_selection(self) -> Self:
        enabled = set(self.enabled_axes)
        excluded = set(self.excluded_axes)
        if len(enabled) != len(self.enabled_axes) or len(excluded) != len(self.excluded_axes):
            raise ValueError("Agent selection cannot contain duplicate axes.")
        if enabled & excluded:
            raise ValueError("Enabled and excluded axes must be disjoint.")
        if not set(self.unavailable_axes) <= excluded:
            raise ValueError("Unavailable axes must be excluded from scoring.")
        if set(self.unavailable_axis_reasons) != set(self.unavailable_axes):
            raise ValueError("Each unavailable axis must have a reason.")
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


class CandidatePosition(BaseModel):
    region_code: str
    fit_summary: str
    selection_condition: str


class CommanderNarrative(BaseModel):
    """Qualitative synthesis across candidates, grounded in comparable evidence."""

    recommended_region_code: str | None
    summary: str
    reasons: list[str] = Field(min_length=1, max_length=3)
    tradeoffs: list[str] = Field(min_length=1, max_length=3)
    next_checks: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    proposal_title: str = ""
    recommendation_strength: Literal["recommended", "conditional", "hypothesis", "undecided"] = (
        "conditional"
    )
    supporting_metric_codes: list[str] = Field(default_factory=list, max_length=6)
    candidate_positions: list[CandidatePosition] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_japanese_text(self) -> Self:
        text_fields = [
            self.proposal_title,
            self.summary,
            *self.reasons,
            *self.tradeoffs,
            *self.next_checks,
        ]
        text_fields.extend(
            text
            for position in self.candidate_positions
            for text in (position.fit_summary, position.selection_condition)
        )
        for text in text_fields:
            if "\ufffc" in text or re.search(r"\[\s*(?:\.\.\.|…)\s*EOL\s*\]", text, re.IGNORECASE):
                raise ValueError("コマンダーの文章に出力制御マーカーが含まれています。")
            for character in text:
                if character.isascii():
                    continue
                name = unicodedata.name(character, "")
                category = unicodedata.category(character)
                if name.startswith(
                    (
                        "CJK UNIFIED IDEOGRAPH",
                        "CJK COMPATIBILITY IDEOGRAPH",
                        "HIRAGANA",
                        "KATAKANA",
                        "LATIN",
                        "FULLWIDTH",
                        "HALFWIDTH",
                        "IDEOGRAPHIC",
                    )
                ) or category[0] in {"M", "N", "P", "S", "Z"}:
                    continue
                raise ValueError("コマンダーの文章には日本語以外の文字が含まれています。")
        return self


class CandidateComparison(BaseModel):
    recommended_region_code: str | None
    shared_axes: list[Axis]
    narrative: CommanderNarrative
    used_fallback: bool = False


class KnowledgeOnlyAxisAssessment(BaseModel):
    """A deliberately approximate axis view produced without external data."""

    axis: Axis
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    narrative: AxisNarrative


class KnowledgeOnlyAssessment(BaseModel):
    """Structured output for the no-external-data assessment path."""

    region_name: str
    axis_assessments: list[KnowledgeOnlyAxisAssessment] = Field(min_length=1, max_length=5)
    narrative: FinalNarrative


class ExecutionStep(BaseModel):
    name: str
    status: Literal["completed", "fallback", "failed", "skipped"]
    elapsed_ms: int
    detail: str


class ReportArtifacts(BaseModel):
    markdown_path: str = ""
    json_path: str = ""


class ResearchSource(BaseModel):
    url: str
    title: str


class SearchStep(BaseModel):
    round: int
    query: str
    summary: str
    sources: list[ResearchSource]


class ResearchContext(BaseModel):
    method: Literal["data_context", "web_search", "knowledge_only", "mock"]
    status: Literal["verified", "searched", "offline", "failed"]
    controlled_fields: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    search_rounds: int = 0
    max_search_rounds: int = 0
    sources: list[ResearchSource] = Field(default_factory=list)
    steps: list[SearchStep] = Field(default_factory=list)


class AssessmentReport(BaseModel):
    report_id: str
    generated_at: datetime
    plan: AssessmentPlan
    research_confidence: float = Field(ge=0, le=1)
    axis_results: list[AxisResult]
    narrative: FinalNarrative
    execution_steps: list[ExecutionStep]
    total_elapsed_ms: int
    artifacts: ReportArtifacts = Field(default_factory=ReportArtifacts)
    disclaimers: list[str]
    research_context: ResearchContext | None = None
