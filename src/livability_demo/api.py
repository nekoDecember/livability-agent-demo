from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .api_models import (
    AssessmentRequest,
    AssessmentResponse,
    ChatCompletionMessage,
    ChatCompletionRequest,
)
from .config import Settings
from .orchestrator import LivabilityOrchestrator

MODEL_ID = "livability-agent"


def _bearer_value(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


async def require_auth(request: Request) -> None:
    expected_secret = request.app.state.settings.livability_api_key
    if expected_secret is None or not expected_secret.get_secret_value():
        return
    expected = expected_secret.get_secret_value()
    received = _bearer_value(request.headers.get("authorization"))
    if received is None or not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token.")


def _last_user_message(messages: list[ChatCompletionMessage]) -> ChatCompletionMessage:
    for message in reversed(messages):
        if message.role == "user" and message.text().strip():
            return message
    raise HTTPException(status_code=400, detail="At least one non-empty user message is required.")


def _usage(prompt: str, response: str) -> dict[str, int]:
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(response) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _completion_json(
    *,
    completion_id: str,
    text: str,
    prompt: str,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(prompt, text),
        "system_fingerprint": "livability-agent-v1",
    }


def _sse_event(event: str, payload: Any) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )


async def _completion_stream(
    *,
    completion_id: str,
    text: str,
) -> AsyncIterator[str]:
    created = int(time.time())
    first = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
    for start in range(0, len(text), 96):
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": text[start : start + 96]},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def create_app(
    settings: Settings | None = None,
    *,
    orchestrator: LivabilityOrchestrator | None = None,
) -> FastAPI:
    active_settings = settings or (orchestrator.settings if orchestrator else Settings())
    owns_orchestrator = orchestrator is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings.validate_api_runtime()
        active_settings.ensure_outputs_dir()
        app.state.settings = active_settings
        app.state.orchestrator = orchestrator or LivabilityOrchestrator(active_settings)
        yield
        if owns_orchestrator:
            await app.state.orchestrator.close()

    app = FastAPI(
        title="Livability Multi-Agent API",
        version="0.2.0",
        description=(
            "OpenWebUI Pipe and OpenAI-compatible transport for the Microsoft Agent "
            "Framework livability workflow"
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        del request
        return JSONResponse(status_code=400, content={"error": {"message": str(exc)}})

    @app.get("/healthz")
    async def health(request: Request) -> dict[str, str]:
        runtime = request.app.state.settings
        return {
            "status": "ok",
            "llm_mode": runtime.resolved_llm_mode,
            "data_mode": runtime.data_mode,
        }

    @app.get("/v1/models", dependencies=[Depends(require_auth)])
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": 0,
                    "owned_by": "livability-agent-demo",
                    "capabilities": {
                        "streaming": True,
                        "files": False,
                        "tools": False,
                    },
                }
            ],
        }

    @app.post(
        "/v1/agent/assessments",
        response_model=AssessmentResponse,
        dependencies=[Depends(require_auth)],
    )
    async def assessment(request: Request, body: AssessmentRequest) -> AssessmentResponse:
        report, markdown = await request.app.state.orchestrator.assess(
            body.request,
            enabled_axes=body.enabled_axes,
            mode=body.mode,
        )
        return AssessmentResponse(report=report, markdown=markdown)

    @app.post("/v1/agent/assessments/stream", dependencies=[Depends(require_auth)])
    async def assessment_stream(request: Request, body: AssessmentRequest):
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def progress(message: str) -> None:
            await queue.put(message)

        task = asyncio.create_task(
            request.app.state.orchestrator.assess(
                body.request,
                progress=progress,
                enabled_axes=body.enabled_axes,
                mode=body.mode,
            )
        )

        async def events() -> AsyncIterator[str]:
            try:
                while not task.done() or not queue.empty():
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    yield _sse_event("progress", {"message": message})

                report, markdown = await task
                yield _sse_event(
                    "result",
                    AssessmentResponse(report=report, markdown=markdown).model_dump(mode="json"),
                )
                yield _sse_event("done", {"report_id": report.report_id})
            except asyncio.CancelledError:
                if not task.done():
                    task.cancel()
                raise
            except Exception as exc:
                yield _sse_event(
                    "error",
                    {"message": str(exc), "type": type(exc).__name__},
                )
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/v1/chat/completions", dependencies=[Depends(require_auth)])
    async def chat_completion(request: Request, body: ChatCompletionRequest):
        if body.model != MODEL_ID:
            raise HTTPException(status_code=404, detail=f"Unknown model: {body.model}")
        prompt = _last_user_message(body.messages).text()
        _, markdown = await request.app.state.orchestrator.assess(
            prompt,
            enabled_axes=body.enabled_axes,
            mode=body.mode,
        )
        completion_id = f"chatcmpl-{uuid4()}"
        if body.stream:
            return StreamingResponse(
                _completion_stream(completion_id=completion_id, text=markdown),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        return _completion_json(
            completion_id=completion_id,
            text=markdown,
            prompt=prompt,
        )

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    settings.validate_api_runtime()
    uvicorn.run(
        "livability_demo.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


__all__ = ["MODEL_ID", "app", "create_app", "main"]
