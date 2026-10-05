from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from livability_demo.api import create_app
from livability_demo.api_models import SearchComparisonRequest
from livability_demo.config import Settings
from livability_demo.models import CommanderNarrative
from livability_demo.web_search import (
    SEARCH_DOMAINS,
    WebCandidateAnswer,
    WebComparisonAnswer,
    WebSearchComparison,
)


def request() -> SearchComparisonRequest:
    return SearchComparisonRequest(
        request="子育てを重視して都市を比較したい",
        regions=[
            {"name": "高崎市", "municipality_code": "10202"},
            {"name": "前橋市", "municipality_code": "10201"},
        ],
    )


def answer() -> WebComparisonAnswer:
    return WebComparisonAnswer(
        candidates=[
            WebCandidateAnswer(
                region_code=code,
                summary="子育ての条件を公式情報で確認する候補です[1]。",
                strengths=["公表された地域の情報を確認できます[1]。"],
                cautions=["希望する保育施設の空きを確認してください。"],
                next_checks=["希望する生活圏を確認してください。"],
            )
            for code in ("10202", "10201")
        ],
        narrative=CommanderNarrative(
            recommended_region_code=None,
            summary="希望に合う保育施設の空きが分からず、選び分けは保留します[1]。",
            reasons=["地域の統計はありますが、施設の空きは確認できません[1]。"],
            tradeoffs=["入園条件と通勤の条件を一緒に確認する必要があります。"],
            next_checks=["候補施設の空きを問い合わせてください。"],
            confidence=0.4,
            recommendation_strength="undecided",
            candidate_positions=[
                {
                    "region_code": code,
                    "fit_summary": "条件を確認する候補",
                    "selection_condition": "空きを確認",
                }
                for code in ("10202", "10201")
            ],
        ),
    )


class SearchResult:
    output_text = "地域の公開情報を確認しました。"

    def model_dump(self):
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "type": "search",
                        "query": "高崎市 前橋市 子育て 統計",
                        "sources": [{"url": "https://www.stat.go.jp/data/", "title": "統計局"}],
                    },
                }
            ]
        }


class FakeResponses:
    def __init__(self, *, fail=False, bad_citation=False):
        self.search_calls = []
        self.answer_calls = []
        self.fail = fail
        self.bad_citation = bad_citation

    async def create(self, **kwargs):
        self.search_calls.append(kwargs)
        if self.fail:
            raise RuntimeError("http://user:proxy-secret@proxy.invalid:8080")
        return SearchResult()

    async def parse(self, **kwargs):
        self.answer_calls.append(kwargs)
        result = answer()
        if self.bad_citation:
            result.narrative.summary = "根拠のない引用[99]"
        return SimpleNamespace(output_parsed=result)


async def no_progress(_):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds", [3, 4])
async def test_search_budget_is_shared_and_answers_keep_real_sources(tmp_path, rounds):
    settings = Settings(
        _env_file=None,
        llm_mode="openai",
        openai_api_key="test-key",
        outputs_dir=tmp_path,
        web_search_rounds=rounds,
    )
    responses = FakeResponses()
    worker = WebSearchComparison(settings, client=SimpleNamespace(responses=responses))
    result = await worker.run(request(), no_progress)
    assert len(responses.search_calls) == rounds  # not rounds per municipality
    assert len(responses.answer_calls) == 1
    for call in responses.search_calls:
        assert call["max_tool_calls"] == 1
        assert call["parallel_tool_calls"] is False
        assert call["tools"][0]["filters"]["allowed_domains"] == list(SEARCH_DOMAINS)
        assert call["store"] is False
    assert "tools" not in responses.answer_calls[0]
    assert result.comparison.shared_axes == []
    assert not result.comparison.used_fallback
    for candidate in result.candidates:
        assert candidate.report.axis_results == []  # never fabricate numeric evidence
        assert candidate.report.research_context.search_rounds == rounds
        assert len(candidate.report.research_context.sources) == 1
        assert "[1](https://www.stat.go.jp/data/)" in candidate.markdown
        assert candidate.report.plan.data_mode == "web_search"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "citation"])
async def test_search_failure_never_leaks_credentials_or_claims_knowledge_fallback(
    tmp_path, failure
):
    settings = Settings(
        _env_file=None,
        llm_mode="openai",
        openai_api_key="test-key",
        outputs_dir=tmp_path,
    )
    responses = FakeResponses(fail=failure == "connection", bad_citation=failure == "citation")
    worker = WebSearchComparison(settings, client=SimpleNamespace(responses=responses))
    with pytest.raises(ValueError) as caught:
        await worker.run(request(), no_progress)
    assert "proxy-secret" not in str(caught.value)
    assert "代替は行っていません" in str(caught.value)
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.asyncio
async def test_mock_has_zero_searches_and_no_recommendation(tmp_path):
    settings = Settings(_env_file=None, llm_mode="mock", outputs_dir=tmp_path)
    responses = FakeResponses()
    worker = WebSearchComparison(settings, client=SimpleNamespace(responses=responses))
    result = await worker.run(request(), no_progress)
    assert responses.search_calls == []
    assert result.comparison.recommended_region_code is None
    assert result.comparison.used_fallback
    for candidate in result.candidates:
        context = candidate.report.research_context
        assert context.status == "offline"
        assert context.search_rounds == 0
        assert context.sources == []
        assert context.steps == []
        assert "Web検索は未実行" in candidate.markdown


@pytest.mark.asyncio
async def test_search_endpoint_auth_sse_and_validation(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="mock",
        api_host="0.0.0.0",
        livability_api_key="test-secret",
        outputs_dir=tmp_path,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = request().model_dump(mode="json")
            response = await client.post("/v1/agent/search-comparisons/stream", json=body)
            assert response.status_code == 401
            headers = {"Authorization": "Bearer test-secret"}
            response = await client.post(
                "/v1/agent/search-comparisons/stream", json=body, headers=headers
            )
            assert response.status_code == 200
            assert "event: result" in response.text
            assert '"search_rounds": 0' in response.text
            body["regions"][1] = body["regions"][0]
            response = await client.post("/v1/agent/search-comparisons", json=body, headers=headers)
            assert response.status_code == 422
