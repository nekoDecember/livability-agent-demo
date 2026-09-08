import asyncio
import json
from types import SimpleNamespace

import pytest

from livability_demo.agent_team import AgentTeam
from livability_demo.config import Settings
from livability_demo.data_providers import MockRegionalDataProvider
from livability_demo.deterministic_analysis import build_axis_narrative
from livability_demo.models import Axis
from livability_demo.offline_client import PAYLOAD_MARKER
from livability_demo.orchestrator import LivabilityOrchestrator
from livability_demo.planning import build_plan
from livability_demo.scoring import score_axis, score_overall


def settings(tmp_path, **kwargs):
    return Settings(
        _env_file=None, llm_mode="mock", data_mode="mock", outputs_dir=tmp_path,
        mock_latency_ms=0, **kwargs,
    )


class Specialist:
    def __init__(self, evidence, *, broken=False):
        self.evidence = evidence
        self.broken = broken
        self.calls = 0
        self.sessions = []

    def create_session(self):
        return object()

    async def run(self, prompt, *, session):
        self.calls += 1
        self.sessions.append(session)
        payload = json.loads(prompt.split(PAYLOAD_MARKER + "\n", 1)[1])
        assert list(payload["evidence_by_axis"]) == [self.evidence.axis.value]
        if self.broken:
            raise ValueError("bad specialist JSON")
        narrative = build_axis_narrative(self.evidence)
        narrative.summary = "Retain this successful specialist result"
        return SimpleNamespace(value=narrative)


@pytest.mark.asyncio
async def test_only_failed_specialist_retries_and_falls_back(tmp_path):
    provider = MockRegionalDataProvider(latency_ms=0)
    region = await provider.resolve_region("流山市")
    evidence = {
        axis: await provider.fetch_axis(region, axis) for axis in [Axis.HOUSING, Axis.FAMILY]
    }
    team = AgentTeam(settings(tmp_path))
    good = Specialist(evidence[Axis.HOUSING])
    bad = Specialist(evidence[Axis.FAMILY], broken=True)
    team.specialists.update({Axis.HOUSING: good, Axis.FAMILY: bad})
    try:
        narratives, fallback, detail = await team.analyze_axes(evidence)
    finally:
        await team.close()
    assert good.calls == 1
    assert bad.calls == 2
    assert bad.sessions[0] is not bad.sessions[1]
    assert narratives[Axis.HOUSING].summary.startswith("Retain")
    assert fallback and "医療・子育て" in detail


@pytest.mark.asyncio
async def test_data_failure_drains_siblings_and_never_runs_agents(tmp_path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Provider(MockRegionalDataProvider):
        async def fetch_axis(self, region, axis):
            if axis == Axis.HOUSING:
                await started.wait()
                raise RuntimeError("Missing live evidence")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    orchestrator = LivabilityOrchestrator(settings(tmp_path), provider=Provider())
    try:
        with pytest.raises(RuntimeError, match="Missing live evidence"):
            await orchestrator.assess("流山市", enabled_axes=[Axis.HOUSING, Axis.FAMILY])
    finally:
        await orchestrator.close()
    assert cancelled.is_set()
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_overall_score_requires_complete_selected_axes(tmp_path):
    provider = MockRegionalDataProvider(latency_ms=0)
    region = await provider.resolve_region("流山市")
    plan = build_plan("流山市", region, data_mode="mock", enabled_axes=[Axis.HOUSING, Axis.FAMILY])
    evidence = await provider.fetch_axis(region, Axis.HOUSING)
    result = score_axis(evidence, build_axis_narrative(evidence))
    for results in ([result], [result, result]):
        with pytest.raises(ValueError, match="exactly one"):
            score_overall(plan, results)


@pytest.mark.asyncio
async def test_context_metrics_do_not_affect_score_and_missing_quality_reduces_confidence():
    provider = MockRegionalDataProvider(latency_ms=0)
    region = await provider.resolve_region("流山市")
    evidence = await provider.fetch_axis(region, Axis.HOUSING)
    metric = evidence.metrics[0]
    evidence.metrics = [
        metric.model_copy(update={"weight": 1, "quality": 1, "normalized_score": 80}),
        metric.model_copy(update={"weight": 3, "quality": 0, "normalized_score": 0}),
        metric.model_copy(update={"weight": 100, "quality": 1, "direction": "context_only"}),
    ]
    result = score_axis(evidence, build_axis_narrative(evidence))
    assert result.score == 80
    assert result.confidence == 0.25
