from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import AssessmentReport, Axis


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=10_000)
    enabled_axes: list[Axis] | None = None


class AssessmentResponse(BaseModel):
    report: AssessmentReport
    markdown: str


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
