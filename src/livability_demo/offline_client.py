from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)

from .deterministic_analysis import (
    build_axis_narrative,
    build_commander_narrative,
    build_knowledge_only_assessment,
)
from .models import (
    Axis,
    AxisEvidence,
    AxisNarrative,
    CommanderNarrative,
    KnowledgeOnlyAssessment,
)

PAYLOAD_MARKER = "PAYLOAD_JSON:"


def _extract_payload(messages: Sequence[Message]) -> dict[str, Any]:
    text = "\n".join(message.text for message in messages)
    marker_index = text.rfind(PAYLOAD_MARKER)
    if marker_index < 0:
        return {}
    payload_text = text[marker_index + len(PAYLOAD_MARKER) :].strip()
    return json.loads(payload_text)


def _system_text(messages: Sequence[Message]) -> str:
    # Agent Framework may represent instructions as a provider-specific instruction role.
    # Searching all messages is safe because the marker is an internal constant that is not
    # exposed as a user option.
    return "\n".join(message.text for message in messages)


class OfflineChatClient(FunctionInvocationLayer, BaseChatClient):
    """Key-free deterministic client for DevUI and tests.

    OpenAIChatClient remains the live client. This client only mirrors the output contracts so
    the complete orchestration can be demonstrated before credentials are available.
    """

    OTEL_PROVIDER_NAME = "livability.offline"

    def _build_value(
        self,
        messages: Sequence[Message],
        options: Mapping[str, Any],
    ) -> AxisNarrative | CommanderNarrative | KnowledgeOnlyAssessment | str:
        # Agent Framework 1.13 carries Agent.instructions in chat options rather than
        # materializing a system Message for every provider.
        system = f"{_system_text(messages)}\n{options.get('instructions', '')}"
        payload = _extract_payload(messages)

        if "KNOWLEDGE_ONLY" in system:
            enabled_axes = [Axis(axis) for axis in payload.get("enabled_axes", [])]
            return build_knowledge_only_assessment(
                str(payload.get("region_name", "サンプル市")),
                str(payload.get("user_request", "")),
                enabled_axes,
            )

        axis_match = re.search(r"SPECIALIST_AXIS=([a-z_]+)", system)
        if axis_match:
            axis = axis_match.group(1)
            evidence_dict = payload.get("evidence_by_axis", {}).get(axis)
            if not evidence_dict:
                raise ValueError(f"Offline specialist payload is missing axis={axis}")
            return build_axis_narrative(AxisEvidence.model_validate(evidence_dict))

        if "COMMANDER_COMPARISON" in system:
            return build_commander_narrative(payload)

        return "オフラインモードです。地域評価エージェントから実行してください。"

    async def _create_response(
        self,
        messages: Sequence[Message],
        options: Mapping[str, Any],
    ) -> ChatResponse[Any]:
        validated = await self._validate_options(options)
        value = self._build_value(messages, validated)
        if isinstance(value, (AxisNarrative, CommanderNarrative, KnowledgeOnlyAssessment)):
            text = value.model_dump_json()
        else:
            text = value
        return ChatResponse(
            messages=[Message("assistant", [text])],
            response_id=f"offline-{uuid4()}",
            model="offline-deterministic",
            finish_reason="stop",
            value=value if not isinstance(value, str) else None,
            response_format=validated.get("response_format"),
        )

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any:
        del kwargs
        if not stream:
            return self._create_response(messages, options)

        async def updates():
            response = await self._create_response(messages, options)
            yield ChatResponseUpdate(
                contents=[Content.from_text(text=response.text)],
                role="assistant",
                response_id=response.response_id,
                model=response.model,
                finish_reason="stop",
            )

        return self._build_response_stream(
            updates(),
            response_format=options.get("response_format"),
        )

    def service_url(self) -> str:
        return "offline://livability-demo"


__all__ = ["OfflineChatClient", "PAYLOAD_MARKER", "ResponseStream"]
