from __future__ import annotations

import asyncio
import hashlib
import re
from abc import ABC, abstractmethod
from time import perf_counter

from .api_clients import EStatApiClient, RealEstateLibraryApiClient
from .catalog import METRIC_CATALOG, MetricSpec
from .config import Settings
from .models import (
    ApiCallTrace,
    Axis,
    AxisEvidence,
    MetricEvidence,
    RegionInfo,
    SourceReference,
)


class RegionalDataProvider(ABC):
    @abstractmethod
    async def resolve_region(self, user_request: str) -> RegionInfo: ...

    @abstractmethod
    async def fetch_axis(self, region: RegionInfo, axis: Axis) -> AxisEvidence: ...

    async def close(self) -> None:
        return None


KNOWN_REGIONS: dict[str, tuple[str, str, str]] = {
    "流山市": ("12220", "千葉県", "人口5万～20万人の市"),
    "柏市": ("12217", "千葉県", "人口20万～50万人の市"),
    "武蔵野市": ("13203", "東京都", "人口5万～20万人の市"),
    "横浜市": ("14100", "神奈川県", "人口50万人以上の市"),
    "さいたま市": ("11100", "埼玉県", "人口50万人以上の市"),
    "千代田区": ("13101", "東京都", "東京23区"),
}


REGION_AXIS_BIAS: dict[str, dict[Axis, float]] = {
    "流山市": {Axis.FAMILY: 10, Axis.FUTURE: 16, Axis.HOUSING: -5},
    "柏市": {Axis.CONVENIENCE: 8, Axis.FAMILY: 5, Axis.HOUSING: -3},
    "武蔵野市": {Axis.CONVENIENCE: 16, Axis.FAMILY: 8, Axis.HOUSING: -20},
    "横浜市": {Axis.CONVENIENCE: 10, Axis.HOUSING: -10, Axis.SAFETY: -2},
    "さいたま市": {Axis.CONVENIENCE: 9, Axis.FUTURE: 5},
    "千代田区": {Axis.CONVENIENCE: 25, Axis.HOUSING: -30, Axis.FUTURE: 6},
}


def _stable_number(*parts: str, modulo: int = 10_000) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulo


def _extract_region_name(user_request: str) -> str:
    for name in sorted(KNOWN_REGIONS, key=len, reverse=True):
        if name in user_request:
            return name

    candidates = re.findall(r"[一-龥々ヶぁ-んァ-ヶー]{1,16}(?:市|区|町|村)", user_request)
    if candidates:
        candidate = candidates[0]
        for prefix in ("について", "では", "なら", "次に", "あと"):
            candidate = candidate.removeprefix(prefix)
        return candidate
    return "サンプル市"


def resolve_region_without_external_data(user_request: str) -> RegionInfo:
    """Resolve a display region using only the local PoC municipality hints.

    The knowledge-only path must not touch a configured live provider because a future
    provider may perform network I/O even during region resolution.
    """

    name = _extract_region_name(user_request)
    known = KNOWN_REGIONS.get(name)
    if known:
        code, prefecture, comparison_group = known
        return RegionInfo(
            query=name,
            name=name,
            municipality_code=code,
            prefecture=prefecture,
            comparison_group=comparison_group,
            is_mock_resolution=False,
        )

    code = f"M{_stable_number(name, modulo=100_000):05d}"
    return RegionInfo(
        query=name,
        name=name,
        municipality_code=code,
        comparison_group="同程度の人口規模の市区町村（デモ設定）",
        is_mock_resolution=True,
    )


class MockRegionalDataProvider(RegionalDataProvider):
    """Deterministic key-free provider used by the report demo."""

    def __init__(self, *, latency_ms: int = 300) -> None:
        self._latency_ms = max(0, latency_ms)

    async def resolve_region(self, user_request: str) -> RegionInfo:
        return resolve_region_without_external_data(user_request)

    async def fetch_axis(self, region: RegionInfo, axis: Axis) -> AxisEvidence:
        started = perf_counter()
        jitter = _stable_number(region.name, axis.value, modulo=180)
        await asyncio.sleep((self._latency_ms + jitter) / 1000)

        metrics = [self._build_metric(region, axis, spec) for spec in METRIC_CATALOG[axis]]
        calls: dict[tuple[str, str], ApiCallTrace] = {}
        for metric in metrics:
            key = (metric.source.source_id, metric.source.endpoint)
            calls[key] = ApiCallTrace(
                tool_name=f"{axis.value}.get_metrics",
                endpoint=metric.source.endpoint,
                source_id=metric.source.source_id,
                status="mocked",
                elapsed_ms=max(1, int((perf_counter() - started) * 1000)),
            )

        elapsed_ms = int((perf_counter() - started) * 1000)
        return AxisEvidence(
            axis=axis,
            region=region,
            metrics=metrics,
            api_calls=list(calls.values()),
            elapsed_ms=elapsed_ms,
            data_mode="mock",
        )

    def _build_metric(self, region: RegionInfo, axis: Axis, spec: MetricSpec) -> MetricEvidence:
        base_score = 28 + _stable_number(region.name, spec.code, modulo=64)
        score = min(96.0, max(4.0, base_score + REGION_AXIS_BIAS.get(region.name, {}).get(axis, 0)))
        position = score / 100
        if spec.direction == "lower_is_better":
            raw_value = spec.raw_max - position * (spec.raw_max - spec.raw_min)
        else:
            raw_value = spec.raw_min + position * (spec.raw_max - spec.raw_min)

        sample_count = None
        quality = 0.82
        if spec.code == "transaction_unit_price":
            sample_count = 10 + _stable_number(region.name, spec.code, modulo=190)
            quality = min(1.0, 0.45 + sample_count / 250)

        return MetricEvidence(
            metric_code=spec.code,
            label=spec.label,
            value=round(raw_value, 1),
            unit=spec.unit,
            normalized_score=round(score, 1),
            direction=spec.direction,  # type: ignore[arg-type]
            weight=spec.weight,
            source=SourceReference(
                source_id=spec.source_id,
                source_name=spec.source_name,
                endpoint=spec.endpoint,
                url=spec.source_url,
                reference_date=spec.reference_date,
                commercial_use_note="利用規約・出典表示条件を社内確認してから本番利用",
            ),
            sample_count=sample_count,
            quality=round(quality, 2),
            note=spec.note,
            is_mock=True,
        )


class GovernmentApiRegionalDataProvider(RegionalDataProvider):
    """Live adapter boundary.

    Low-level HTTP clients are implemented, while indicator-code selection and GIS aggregation
    remain an explicit integration task after API keys and representative responses are available.
    The class fails loudly instead of silently mixing mock and real values.
    """

    def __init__(self, settings: Settings) -> None:
        settings.validate_runtime()
        assert settings.estat_app_id is not None
        assert settings.reinfolib_api_key is not None
        self._estat = EStatApiClient(settings.estat_app_id.get_secret_value())
        self._reinfolib = RealEstateLibraryApiClient(
            settings.reinfolib_api_key.get_secret_value()
        )

    async def resolve_region(self, user_request: str) -> RegionInfo:
        name = _extract_region_name(user_request)
        known = KNOWN_REGIONS.get(name)
        if not known:
            raise ValueError(
                "Live mode currently requires a municipality entry in KNOWN_REGIONS. "
                "Replace this lookup with XIT002-backed municipality master ingestion."
            )
        code, prefecture, comparison_group = known
        return RegionInfo(
            query=name,
            name=name,
            municipality_code=code,
            prefecture=prefecture,
            comparison_group=comparison_group,
        )

    async def fetch_axis(self, region: RegionInfo, axis: Axis) -> AxisEvidence:
        raise NotImplementedError(
            "Government API credentials are wired, but live indicator mappings and GIS "
            f"aggregation for axis={axis.value} must be finalized using contracted sample data. "
            "Use DATA_MODE=mock for the key-free PoC."
        )

    async def close(self) -> None:
        await asyncio.gather(self._estat.close(), self._reinfolib.close())


def build_data_provider(settings: Settings) -> RegionalDataProvider:
    if settings.data_mode == "government_api":
        return GovernmentApiRegionalDataProvider(settings)
    return MockRegionalDataProvider(latency_ms=settings.mock_latency_ms)
