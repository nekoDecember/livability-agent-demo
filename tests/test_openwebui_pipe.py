from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PIPE_PATH = Path(__file__).parents[1] / "openwebui/livability_agent_pipe.py"


def load_pipe_module():
    spec = importlib.util.spec_from_file_location("livability_agent_pipe", PIPE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pipe_extracts_last_multimodal_user_prompt() -> None:
    module = load_pipe_module()
    prompt = module.Pipe._last_user_text(
        {
            "messages": [
                {"role": "user", "content": "古い質問"},
                {"role": "assistant", "content": "応答"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "流山市を評価して"},
                        {"type": "image_url", "image_url": {"url": "ignored"}},
                    ],
                },
            ]
        }
    )
    assert prompt == "流山市を評価して"


def test_pipe_adds_bearer_header_only_when_configured() -> None:
    module = load_pipe_module()
    pipe = module.Pipe()
    pipe.valves.backend_api_key = "secret"
    assert pipe._headers()["Authorization"] == "Bearer secret"
    pipe.valves.backend_api_key = ""
    assert "Authorization" not in pipe._headers()


@pytest.mark.asyncio
async def test_pipe_calls_assessment_api_and_emits_status(monkeypatch) -> None:
    module = load_pipe_module()
    calls = []

    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "report": {
                    "plan": {"enabled_axes": ["convenience", "housing"]},
                    "total_elapsed_ms": 123,
                },
                "markdown": "# 評価結果",
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            calls.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, path, *, json):
            calls.append((path, json))
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    events = []

    async def emitter(event):
        events.append(event)

    pipe = module.Pipe()
    pipe.valves.backend_url = "http://agent-api:8091"
    pipe.valves.backend_api_key = "secret"
    result = await pipe.pipe(
        {"messages": [{"role": "user", "content": "流山市を評価して"}]},
        __event_emitter__=emitter,
    )

    assert result == "# 評価結果"
    assert calls[1] == (
        "/v1/agent/assessments",
        {"request": "流山市を評価して"},
    )
    assert events[0]["data"]["done"] is False
    assert events[-1]["data"] == {
        "description": "2専門Agentで評価完了（123 ms）",
        "done": True,
        "level": "info",
    }


def test_pipe_contains_no_dynamic_code_execution() -> None:
    source = PIPE_PATH.read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "eval(" not in source
    assert "exec(" not in source
