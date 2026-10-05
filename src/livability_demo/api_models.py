from __future__ import annotations

from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import AssessmentReport, Axis, CandidateComparison


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=10_000)
    enabled_axes: list[Axis] | None = None
    weights: dict[Axis, float] | None = None
    # "data" follows the server's configured data provider. "knowledge_only" skips
    # regional data providers and asks the LLM for a deliberately approximate view.
    mode: Literal["data", "knowledge_only"] = "data"


class AssessmentResponse(BaseModel):
    report: AssessmentReport
    markdown: str


class SearchRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    municipality_code: str = Field(pattern=r"^\d{5}$")
    prefecture: str | None = Field(default=None, max_length=100)


class SearchComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=10_000)
    regions: list[SearchRegion] = Field(min_length=2, max_length=4)
    enabled_axes: list[Axis] | None = None
    weights: dict[Axis, float] | None = None

    @model_validator(mode="after")
    def validate_inputs(self) -> SearchComparisonRequest:
        codes = [region.municipality_code for region in self.regions]
        if len(codes) != len(set(codes)):
            raise ValueError("候補地が重複しています。")
        if self.enabled_axes is not None and (
            not self.enabled_axes or len(self.enabled_axes) != len(set(self.enabled_axes))
        ):
            raise ValueError("比較する視点を重複なく1つ以上指定してください。")
        if self.weights and (
            any(not isfinite(value) or not 0 <= value <= 100 for value in self.weights.values())
            or sum(self.weights.values()) <= 0
        ):
            raise ValueError("重みは0〜100で、少なくとも1つは正の値にしてください。")
        return self


class SearchComparisonResponse(BaseModel):
    candidates: list[AssessmentResponse]
    comparison: CandidateComparison


class CandidateComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reports: list[AssessmentReport] = Field(min_length=2, max_length=4)
    request: str = Field(min_length=1, max_length=10_000)
    weights: dict[Axis, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_comparison(self) -> CandidateComparisonRequest:
        codes = [report.plan.region.municipality_code for report in self.reports]
        if len(codes) != len(set(codes)):
            raise ValueError("候補地が重複しています。")
        if any(
            not isfinite(weight) or weight < 0 or weight > 100 for weight in self.weights.values()
        ):
            raise ValueError("重みは0〜100の範囲で指定してください。")
        if self.weights and sum(self.weights.values()) <= 0:
            raise ValueError("有効な評価軸の重みを1つ以上指定してください。")
        modes = {report.plan.data_mode for report in self.reports}
        if len(modes) != 1:
            raise ValueError("異なる評価モードのレポートは比較できません。")
        return self


class CandidateComparisonResponse(BaseModel):
    comparison: CandidateComparison


class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if not self.content:
            return ""
        values: list[str] = []
        for part in self.content:
            if part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                values.append(part["text"])
        return "\n".join(values)


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "livability-agent"
    messages: list[ChatCompletionMessage] = Field(min_length=1)
    stream: bool = False
    user: str | None = None
    enabled_axes: list[Axis] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    mode: Literal["data", "knowledge_only"] = "data"
