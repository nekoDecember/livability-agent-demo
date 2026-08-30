from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    AgentSession,
    BaseAgent,
    Content,
    Message,
    ResponseStream,
)

from .config import Settings
from .orchestrator import LivabilityOrchestrator


def _request_text(messages: Any) -> str:
    if messages is None:
        return "流山市の住みやすさを5軸均等で評価して"
    if isinstance(messages, str):
        return messages
    if isinstance(messages, Message):
        return messages.text
    if isinstance(messages, Mapping):
        return str(messages.get("text") or messages.get("content") or messages)
    if isinstance(messages, Sequence):
        user_texts = [
            item.text
            for item in messages
            if isinstance(item, Message) and item.role == "user" and item.text.strip()
        ]
        if user_texts:
            return "\n".join(user_texts)
        return "\n".join(str(item) for item in messages)
    return str(messages)


class LivabilityCoordinatorAgent(BaseAgent):
    """DevUI-facing coordinator that selects and drives the specialist Agent workflow."""

    def __init__(
        self,
        settings: Settings | None = None,
        orchestrator: LivabilityOrchestrator | None = None,
    ) -> None:
        super().__init__(
            id="livability-coordinator",
            name="LivabilityCoordinatorAgent",
            description=(
                "入力地域を最大5軸で評価し、選択した専門エージェントを並列実行して"
                "Markdown/JSONを保存します"
            ),
            additional_properties={"framework": "Microsoft Agent Framework"},
        )
        self.settings = settings or Settings()
        self.orchestrator = orchestrator or LivabilityOrchestrator(self.settings)

    def run(
        self,
        messages: Any = None,
        *,
        stream: bool = False,
        session: AgentSession | None = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        del session, function_invocation_kwargs, client_kwargs
        request = _request_text(messages)
        response_id = f"livability-{uuid4()}"

        if not stream:
            async def complete() -> AgentResponse[Any]:
                report, markdown = await self.orchestrator.assess(request)
                artifact_note = (
                    f"\n\n---\n保存先: `{report.artifacts.markdown_path}` / "
                    f"`{report.artifacts.json_path}`"
                )
                return AgentResponse(
                    messages=[
                        Message("assistant", [markdown + artifact_note], author_name=self.name)
                    ],
                    response_id=response_id,
                    agent_id=self.id,
                    finish_reason="stop",
                )

            return complete()

        async def updates():
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def progress(message: str) -> None:
                await queue.put(message)

            task = asyncio.create_task(self.orchestrator.assess(request, progress=progress))
            message_id = f"msg-{uuid4()}"
            while not task.done() or not queue.empty():
                try:
                    status = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                if status is not None:
                    yield AgentResponseUpdate(
                        contents=[Content.from_text(text=f"> {status}\n\n")],
                        role="assistant",
                        author_name=self.name,
                        agent_id=self.id,
                        response_id=response_id,
                        message_id=message_id,
                    )

            report, markdown = await task
            artifact_note = (
                f"\n\n---\n保存先: `{report.artifacts.markdown_path}` / "
                f"`{report.artifacts.json_path}`"
            )
            yield AgentResponseUpdate(
                contents=[Content.from_text(text=markdown + artifact_note)],
                role="assistant",
                author_name=self.name,
                agent_id=self.id,
                response_id=response_id,
                message_id=message_id,
                finish_reason="stop",
            )

        def finalize(all_updates: Sequence[AgentResponseUpdate]) -> AgentResponse[Any]:
            final_text = all_updates[-1].text if all_updates else "評価結果を生成できませんでした。"
            return AgentResponse(
                messages=[Message("assistant", [final_text], author_name=self.name)],
                response_id=response_id,
                agent_id=self.id,
                finish_reason="stop",
            )

        return ResponseStream(updates(), finalizer=finalize)

    async def close(self) -> None:
        await self.orchestrator.close()
