"""
title: 地域住みやすさ評価マルチエージェント
author: local
version: 0.2.0
description: Microsoft Agent Frameworkの並列Workflowを独立API経由で実行します。
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

EventEmitter = Callable[[dict[str, Any]], Awaitable[None] | None]


class Pipe:
    class Valves(BaseModel):
        backend_url: str = Field(
            default_factory=lambda: os.getenv(
                "LIVABILITY_BACKEND_URL", "http://livability-agent-api:8091"
            ),
            description="住みやすさAgent APIのベースURL",
        )
        backend_api_key: str = Field(
            default_factory=lambda: os.getenv("LIVABILITY_API_KEY", ""),
            description="Agent APIのBearerトークン",
        )
        timeout_seconds: float = Field(
            default_factory=lambda: float(os.getenv("LIVABILITY_PIPE_TIMEOUT_SECONDS", "300")),
            ge=10,
            le=900,
            description="並列評価完了を待つ最大秒数",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def pipes(self) -> list[dict[str, str]]:
        return [{"id": "livability-agent", "name": "地域住みやすさ評価"}]

    async def pipe(
        self,
        body: dict[str, Any],
        __metadata__: dict[str, Any] | None = None,
        __event_emitter__: EventEmitter | None = None,
        **_: Any,
    ) -> str:
        metadata = __metadata__ or {}
        prompt = str(metadata.get("user_prompt") or self._last_user_text(body)).strip()
        if not prompt:
            return "評価する市区町村を入力してください。"

        await self._emit(
            __event_emitter__,
            "地域を特定し、選択した専門Agentを並列実行しています",
            done=False,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.valves.backend_url.rstrip("/"),
                headers=self._headers(),
                timeout=self.valves.timeout_seconds,
            ) as client:
                response = await client.post(
                    "/v1/agent/assessments",
                    json={"request": prompt},
                )
                self._raise_backend_error(response)
                result = response.json()
        except Exception as exc:
            await self._emit(
                __event_emitter__,
                f"住みやすさAgent APIへの接続に失敗しました: {exc}",
                done=True,
                level="error",
            )
            return f"住みやすさ評価を実行できませんでした: {exc}"

        report = result.get("report") or {}
        plan = report.get("plan") or {}
        enabled_count = len(plan.get("enabled_axes") or [])
        elapsed_ms = report.get("total_elapsed_ms")
        detail = f"{enabled_count}専門Agentで評価完了"
        if isinstance(elapsed_ms, int):
            detail += f"（{elapsed_ms} ms）"
        await self._emit(__event_emitter__, detail, done=True)
        return str(result.get("markdown") or "評価結果が空でした。")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.valves.backend_api_key:
            headers["Authorization"] = f"Bearer {self.valves.backend_api_key}"
        return headers

    @staticmethod
    def _last_user_text(body: dict[str, Any]) -> str:
        for message in reversed(body.get("messages") or []):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                values = [
                    part["text"]
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") in {"text", "input_text"}
                    and isinstance(part.get("text"), str)
                ]
                return "\n".join(values)
        return ""

    @staticmethod
    def _raise_backend_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        message = "Agent API returned an error."
        try:
            payload = response.json()
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or message)
            elif payload.get("detail"):
                message = str(payload["detail"])
        except (AttributeError, ValueError):
            pass
        raise RuntimeError(f"HTTP {response.status_code}: {message}")

    @staticmethod
    async def _emit(
        emitter: EventEmitter | None,
        description: str,
        *,
        done: bool,
        level: str = "info",
    ) -> None:
        if emitter is None:
            return
        result = emitter(
            {
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                    "level": level,
                },
            }
        )
        if inspect.isawaitable(result):
            await result
