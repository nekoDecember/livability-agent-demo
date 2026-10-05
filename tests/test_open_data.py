from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from livability_demo.config import Settings
from livability_demo.data_providers import OpenDataRegionalDataProvider
from livability_demo.deterministic_analysis import build_axis_narrative
from livability_demo.models import Axis
from livability_demo.open_data import (
    OpenDataMetricRow,
    build_open_data_snapshot,
    load_open_data_snapshot,
)
from livability_demo.orchestrator import LivabilityOrchestrator
from livability_demo.scoring import score_axis

METRIC_ROWS = (
    (Axis.CONVENIENCE, "station_density", 20),
    (Axis.HOUSING, "official_land_price", 40),
    (Axis.FAMILY, "clinics_per_100k", 80),
    (Axis.SAFETY, "crime_rate", 5),
    (Axis.FUTURE, "population_retention_2040", 90),
)


def _write_inputs(
    root: Path,
    *,
    metric_rows: tuple[tuple[Axis, str, int], ...] = METRIC_ROWS,
    license_id: str = "COMMERCIAL-USE-ALLOWED",
    download_page_url: str = "https://www.e-stat.go.jp/regional-statistics/ssdsview/",
    license_url: str = "https://www.e-stat.go.jp/terms-of-use",
) -> tuple[Path, Path]:
    original = root / "official-download.csv"
    original.write_text("official,download\n1,2\n", encoding="utf-8")

    sources = root / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "ESTAT-TEST",
                        "source_name": "e-Stat test fixture",
                        "publisher": "総務省統計局",
                        "download_page_url": download_page_url,
                        "license_id": license_id,
                        "license_url": license_url,
                        "attribution": "政府統計の総合窓口（e-Stat）を加工",
                        "retrieved_at": "2026-09-20",
                        "reference_date": "2023",
                        "original_file": original.name,
                        "commercial_use_note": "商用利用可と出典表示条件を確認",
                        "third_party_rights_note": "第三者権利表示なしを確認",
                        "transformation_note": "自治体コードで抽出。欠損補完なし",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metrics = root / "normalized.csv"
    with metrics.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "region_code",
                "region_name",
                "prefecture",
                "comparison_group",
                "axis",
                "metric_code",
                "value",
                "quality",
                "source_id",
                "sample_count",
                "note",
            ],
        )
        writer.writeheader()
        for axis, metric_code, value in metric_rows:
            writer.writerow(
                {
                    "region_code": "12220",
                    "region_name": "流山市",
                    "prefecture": "千葉県",
                    "comparison_group": "人口5万～20万人の市",
                    "axis": axis.value,
                    "metric_code": metric_code,
                    "value": value,
                    "quality": 0.9,
                    "source_id": "ESTAT-TEST",
                    "sample_count": "",
                    "note": "test normalization",
                }
            )
    return metrics, sources


def _build_snapshot(
    root: Path,
    *,
    metric_rows: tuple[tuple[Axis, str, int], ...] = METRIC_ROWS,
) -> Path:
    metrics, sources = _write_inputs(root, metric_rows=metric_rows)
    output = root / "snapshot"
    build_open_data_snapshot(
        metrics_path=metrics,
        sources_path=sources,
        output_dir=output,
    )
    return output


@pytest.mark.asyncio
async def test_open_data_provider_uses_real_rows_and_marks_missing_metrics(tmp_path: Path) -> None:
    snapshot = _build_snapshot(tmp_path)
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="open_data",
        open_data_dir=snapshot,
        outputs_dir=tmp_path / "outputs",
    )
    provider = OpenDataRegionalDataProvider(settings)

    region = await provider.resolve_region("千葉県流山市を評価して")
    selected = await provider.resolve_region(
        "対象自治体コード: 12220。流山市を評価して。条件: 勤務地:前橋市"
    )
    assert selected.municipality_code == region.municipality_code
    assert any(item.municipality_code == "12220" for item in await provider.list_regions())
    evidence = await provider.fetch_axis(region, Axis.HOUSING)

    official = next(metric for metric in evidence.metrics if metric.quality > 0)
    missing = [metric for metric in evidence.metrics if metric.quality == 0]
    assert official.metric_code == "official_land_price"
    assert official.is_mock is False
    assert official.source.source_id == "ESTAT-TEST"
    assert "COMMERCIAL-USE-ALLOWED" in official.source.commercial_use_note
    assert len(missing) == 3
    assert {metric.metric_code for metric in missing} == {
        "transaction_unit_price",
        "housing_stock",
        "residential_land_price",
    }
    assert all(metric.source.source_id == "UNAVAILABLE" for metric in missing)
    assert all(metric.missing_reason == "not_collected" for metric in missing)
    assert all("元データに存在しないとは限りません" in (metric.note or "") for metric in missing)

    result = score_axis(evidence, build_axis_narrative(evidence))
    assert result.score == official.normalized_score
    assert result.confidence == 0.23


@pytest.mark.asyncio
async def test_missing_metric_distinguishes_region_gap_from_uncollected_indicator(
    tmp_path: Path,
) -> None:
    metrics, sources = _write_inputs(tmp_path)
    with metrics.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "12217",
                "柏市",
                "千葉県",
                "人口20万～50万人の市",
                "housing",
                "transaction_unit_price",
                40,
                0.9,
                "ESTAT-TEST",
                "",
                "test",
            ]
        )
    snapshot = tmp_path / "snapshot"
    build_open_data_snapshot(metrics_path=metrics, sources_path=sources, output_dir=snapshot)
    provider = OpenDataRegionalDataProvider(
        Settings(
            _env_file=None,
            llm_mode="mock",
            data_mode="open_data",
            open_data_dir=snapshot,
            outputs_dir=tmp_path / "outputs",
        )
    )
    region = await provider.resolve_region("流山市")
    evidence = await provider.fetch_axis(region, Axis.HOUSING)
    reasons = {metric.metric_code: metric.missing_reason for metric in evidence.metrics}
    assert reasons["transaction_unit_price"] == "not_found_for_region"
    assert reasons["housing_stock"] == "not_collected"


def test_unmatched_traffic_code_is_not_a_measured_zero() -> None:
    from livability_demo.models import RegionInfo

    row = OpenDataMetricRow(
        region=RegionInfo(query="高崎市", name="高崎市", municipality_code="10202"),
        axis=Axis.SAFETY,
        metric_code="traffic_accidents",
        value=0,
        quality=0.9,
        source_id="NPA-TRAFFIC-OPEN-DATA-test",
        sample_count=0,
        note=None,
    )
    assert not OpenDataRegionalDataProvider._verified_row(row)
    assert OpenDataRegionalDataProvider._verified_row(replace(row, value=10, sample_count=20))


@pytest.mark.asyncio
async def test_open_data_mode_runs_existing_five_axis_workflow(tmp_path: Path) -> None:
    snapshot = _build_snapshot(tmp_path)
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="open_data",
        open_data_dir=snapshot,
        outputs_dir=tmp_path / "outputs",
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, markdown = await orchestrator.assess("流山市を5軸で評価して")
    finally:
        await orchestrator.close()

    assert report.plan.data_mode == "open_data"
    assert len(report.axis_results) == len(Axis)
    assert all(not metric.is_mock for result in report.axis_results for metric in result.metrics)
    assert all(result.confidence < 1 for result in report.axis_results)
    assert "公式公開データのスナップショット" in markdown
    assert "未取得" in markdown


@pytest.mark.asyncio
async def test_open_data_mode_excludes_axis_with_no_official_rows(tmp_path: Path) -> None:
    snapshot = _build_snapshot(tmp_path, metric_rows=METRIC_ROWS[:-1])
    settings = Settings(
        _env_file=None,
        llm_mode="mock",
        data_mode="open_data",
        open_data_dir=snapshot,
        outputs_dir=tmp_path / "outputs",
        mock_latency_ms=0,
        devui_auto_open=False,
    )
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, _ = await orchestrator.assess("流山市を評価して")
    finally:
        await orchestrator.close()

    assert Axis.FUTURE in report.plan.excluded_axes
    assert report.plan.unavailable_axis_reasons[Axis.FUTURE] == "not_collected"
    assert Axis.FUTURE not in report.plan.enabled_axes
    assert len(report.axis_results) == 4
    assert any("まちの将来性" in item and "値を確認できない" in item for item in report.disclaimers)


@pytest.mark.parametrize(
    ("license_id", "download_page_url", "message"),
    [
        ("NON-COMMERCIAL", "https://www.e-stat.go.jp/", "license_id"),
        ("CC-BY-4.0", "https://example.com/data.csv", "official .go.jp or .lg.jp"),
    ],
)
def test_snapshot_rejects_unapproved_sources(
    tmp_path: Path,
    license_id: str,
    download_page_url: str,
    message: str,
) -> None:
    metrics, sources = _write_inputs(
        tmp_path,
        license_id=license_id,
        download_page_url=download_page_url,
    )
    with pytest.raises(ValueError, match=message):
        build_open_data_snapshot(
            metrics_path=metrics,
            sources_path=sources,
            output_dir=tmp_path / "snapshot",
        )


def test_snapshot_rejects_metrics_modified_after_manifest(tmp_path: Path) -> None:
    snapshot = _build_snapshot(tmp_path)
    with (snapshot / "metrics.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")

    with pytest.raises(ValueError, match="SHA-256不一致"):
        load_open_data_snapshot(snapshot)


def test_snapshot_rejects_unapproved_license_host(tmp_path: Path) -> None:
    metrics, sources = _write_inputs(
        tmp_path,
        license_url="https://example.com/license",
    )
    with pytest.raises(ValueError, match="approved license-authority host"):
        build_open_data_snapshot(
            metrics_path=metrics,
            sources_path=sources,
            output_dir=tmp_path / "snapshot",
        )
