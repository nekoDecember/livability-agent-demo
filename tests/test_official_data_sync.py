from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from livability_demo.models import Axis, RegionInfo
from livability_demo.official_data_sync import (
    CHILD_POPULATION,
    CLINICS_PER_100K,
    DAYTIME_POPULATION_RATIO,
    ELEMENTARY_SCHOOLS,
    FUTURE_TOTAL_POPULATION,
    HIGH_SCHOOLS,
    HOUSEHOLDS,
    HOUSEHOLDS_IN_HOUSING,
    HOUSING_COUNT,
    JUNIOR_HIGH_SCHOOLS,
    LAND_SURVEY_SOURCE_ID,
    NURSERY_FACILITIES,
    RETAIL_ESTABLISHMENTS,
    TERTIARY_SCHOOL_SOURCE_ID,
    TOTAL_POPULATION,
    DashboardPoint,
    ResolvedRegion,
    build_dashboard_metric_rows,
    parse_land_survey_prices,
    parse_npa_traffic_counts,
    parse_tertiary_education_campuses,
    sync_official_open_data,
)
from livability_demo.open_data import OpenDataMetricRow, OpenDataSource, write_open_data_snapshot


def _value(indicator: str, value: float, year: int) -> dict[str, str]:
    return {
        "@indicator": indicator,
        "@regionCode": "12220",
        "@time": f"{year}CY00",
        "$": str(value),
    }


def _land_survey_zip() -> bytes:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.0, 35.0]},
            "properties": {
                "L02_001": "000",
                "L02_005": 2026,
                "L02_006": 50_000,
                "L02_020": "12220",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.1, 35.1]},
            "properties": {
                "L02_001": "000",
                "L02_005": 2026,
                "L02_006": 80_000,
                "L02_020": "12220",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.2, 35.2]},
            "properties": {
                "L02_001": "005",
                "L02_005": 2026,
                "L02_006": 1_000_000,
                "L02_020": "12220",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.3, 35.3]},
            "properties": {
                "L02_001": "000",
                "L02_005": 2025,
                "L02_006": 40_000,
                "L02_020": "12220",
            },
        },
    ]
    features.extend(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.4, 35.4]},
            "properties": {
                "L02_001": "000",
                "L02_005": 2026,
                "L02_006": 25_000,
                "L02_020": "00000",
            },
        }
        for _ in range(996)
    )
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "L02-26.geojson",
            json.dumps({"type": "FeatureCollection", "features": features}),
        )
    return payload.getvalue()


def _tertiary_school_zip() -> bytes:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.0, 35.0]},
            "properties": {
                "P29_001": "12220",
                "P29_002": "F-1",
                "P29_003": "16007",
                "P29_007": "0",
                "P29_008": "00",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.0, 35.0]},
            "properties": {
                "P29_001": "12220",
                "P29_002": "F-1",
                "P29_003": "16007",
                "P29_007": "0",
                "P29_008": "00",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.1, 35.1]},
            "properties": {
                "P29_001": "12220",
                "P29_002": "F-1",
                "P29_003": "16007",
                "P29_007": "0",
                "P29_008": "01",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.2, 35.2]},
            "properties": {
                "P29_001": "12220",
                "P29_002": "F-2",
                "P29_003": "16006",
                "P29_007": "2",
                "P29_008": "00",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.3, 35.3]},
            "properties": {
                "P29_001": "12220",
                "P29_002": "S-1",
                "P29_003": "16003",
                "P29_007": "1",
                "P29_008": "00",
            },
        },
    ]
    features.extend(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [139.4, 35.4]},
            "properties": {
                "P29_001": "00000",
                "P29_002": f"F-{index}",
                "P29_003": "16007",
                "P29_007": "0",
                "P29_008": "00",
            },
        }
        for index in range(1_000)
    )
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "P29-23.geojson",
            json.dumps({"type": "FeatureCollection", "features": features}),
        )
    return payload.getvalue()


@pytest.mark.asyncio
async def test_sync_builds_five_axis_snapshot_from_no_account_sources(
    tmp_path: Path,
) -> None:
    values = {
        TOTAL_POPULATION: [_value(TOTAL_POPULATION, 200_000, 2020)],
        CHILD_POPULATION: [_value(CHILD_POPULATION, 30_000, 2020)],
        HOUSEHOLDS: [_value(HOUSEHOLDS, 80_000, 2020)],
        RETAIL_ESTABLISHMENTS: [_value(RETAIL_ESTABLISHMENTS, 2_000, 2021)],
        DAYTIME_POPULATION_RATIO: [_value(DAYTIME_POPULATION_RATIO, 95, 2020)],
        HOUSING_COUNT: [_value(HOUSING_COUNT, 90_000, 2018)],
        NURSERY_FACILITIES: [_value(NURSERY_FACILITIES, 300, 2020)],
        ELEMENTARY_SCHOOLS: [_value(ELEMENTARY_SCHOOLS, 30, 2020)],
        JUNIOR_HIGH_SCHOOLS: [_value(JUNIOR_HIGH_SCHOOLS, 15, 2020)],
        HIGH_SCHOOLS: [_value(HIGH_SCHOOLS, 5, 2020)],
        FUTURE_TOTAL_POPULATION: [
            _value(FUTURE_TOTAL_POPULATION, 180_000, 2040),
            _value(FUTURE_TOTAL_POPULATION, 160_000, 2050),
        ],
        CLINICS_PER_100K: [_value(CLINICS_PER_100K, 70, 2020)],
    }
    traffic_csv = (
        "都道府県コード,市区町村コード,事故内容\r\n12,220,負傷\r\n12,220,負傷\r\n"
    ).encode("cp932")
    land_survey_zip = _land_survey_zip()
    tertiary_school_zip = _tertiary_school_zip()
    output = tmp_path / "snapshot"
    existing_source = OpenDataSource(
        source_id="KSJ-EXISTING",
        source_name="既存の国土数値情報集計",
        publisher="国土交通省",
        download_page_url="https://nlftp.mlit.go.jp/ksj/",
        license_id="CC-BY-4.0",
        license_url="https://nlftp.mlit.go.jp/ksj/other/agreement.html",
        attribution="国土数値情報を加工して作成",
        retrieved_at=date(2026, 9, 20),
        reference_date="2023",
        original_filename="existing.zip",
        original_sha256="0" * 64,
        commercial_use_note="CC BY 4.0対象データとして確認",
        third_party_rights_note="第三者権利表示なしを確認",
        transformation_note="自治体境界で駅を集計",
    )
    existing_region = RegionInfo(
        query="流山市",
        name="流山市",
        municipality_code="12220",
        prefecture="千葉県",
        comparison_group="人口5万～20万人の市",
    )
    write_open_data_snapshot(
        rows=[
            OpenDataMetricRow(
                region=existing_region,
                axis=Axis.CONVENIENCE,
                metric_code="station_density",
                value=20,
                quality=0.9,
                source_id=existing_source.source_id,
                sample_count=10,
                note="existing row",
            )
        ],
        sources=[existing_source],
        output_dir=output,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getRegionInfo"):
            return httpx.Response(
                200,
                json={
                    "GET_META_REGION_INF": {
                        "RESULT": {"status": "0"},
                        "METADATA_INF": {
                            "CLASS_INF": {
                                "CLASS_OBJ": {
                                    "@parentRegionCode": "12000",
                                    "@name": "千葉県",
                                    "CLASS": {
                                        "@regionCode": "12220",
                                        "@name": "流山市",
                                        "@toDate": "999912",
                                    },
                                }
                            }
                        },
                    }
                },
            )
        if request.url.path.endswith("/getData"):
            requested = request.url.params["IndicatorCode"].split(",")
            response_values = [item for code in requested for item in values.get(code, [])]
            return httpx.Response(
                200,
                json={
                    "GET_STATS": {
                        "RESULT": {"status": "0"},
                        "STATISTICAL_DATA": {
                            "DATA_INF": {"DATA_OBJ": [{"VALUE": response_values}]}
                        },
                    }
                },
            )
        if request.url.path.endswith("/honhyo_2024.csv"):
            return httpx.Response(200, content=traffic_csv)
        if request.url.path.endswith("/L02-26_GML.zip"):
            return httpx.Response(200, content=land_survey_zip)
        if request.url.path.endswith("/P29-23_GML.zip"):
            return httpx.Response(200, content=tertiary_school_zip)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await sync_official_open_data(
            region_names=("流山市",),
            output_dir=output,
            traffic_csv_url=(
                "https://www.npa.go.jp/publications/statistics/koutsuu/opendata/2024/"
                "honhyo_2024.csv"
            ),
            traffic_reference_year=2024,
            minimum_interval_seconds=0,
            http_client=client,
        )
        nationwide = await sync_official_open_data(
            region_names=(),
            all_regions=True,
            output_dir=tmp_path / "nationwide",
            traffic_csv_url=(
                "https://www.npa.go.jp/publications/statistics/koutsuu/opendata/2024/"
                "honhyo_2024.csv"
            ),
            traffic_reference_year=2024,
            minimum_interval_seconds=0,
            http_client=client,
        )

    assert result.refreshed_metric_count == 11
    assert "KSJ-EXISTING" in result.snapshot.sources
    assert any(
        source_id.startswith("ESTAT-DASHBOARD-API-") for source_id in result.snapshot.sources
    )
    assert any(
        source_id.startswith("NPA-TRAFFIC-OPEN-DATA-") for source_id in result.snapshot.sources
    )
    assert any(
        source_id.startswith(f"{LAND_SURVEY_SOURCE_ID}-") for source_id in result.snapshot.sources
    )
    assert any(
        source_id.startswith(f"{TERTIARY_SCHOOL_SOURCE_ID}-")
        for source_id in result.snapshot.sources
    )
    assert {row.axis for row in result.snapshot.rows} == set(Axis)
    by_code = {row.metric_code: row for row in result.snapshot.rows}
    assert by_code["station_density"].value == 20
    assert by_code["retail_density"].value == 10
    assert by_code["housing_stock"].value == 1125
    assert by_code["nursery_per_1000_children"].value == 10
    assert by_code["schools_per_1000_children"].value == pytest.approx(1.67)
    assert by_code["population_retention_2040"].value == 90
    assert by_code["population_retention_2050"].value == 80
    assert by_code["traffic_accidents"].value == 1
    assert by_code["residential_land_price"].value == 65_000
    assert by_code["residential_land_price"].sample_count == 2
    assert by_code["tertiary_education_campuses"].value == 2
    assert by_code["tertiary_education_campuses"].quality == 0.45
    assert "状態コード0（調査なし）が2件" in by_code["tertiary_education_campuses"].note
    assert (output / "manifest.json").is_file()
    assert len(list(output.glob("honhyo_2024-*.csv"))) == 1
    assert len(nationwide.refreshed_regions) == 1
    assert nationwide.refreshed_metric_count == 11
    nationwide_dashboard_source = next(
        source
        for source_id, source in nationwide.snapshot.sources.items()
        if source_id.startswith("ESTAT-DASHBOARD-API-")
    )
    assert "取得範囲=全国市区町村" in nationwide_dashboard_source.transformation_note


def test_parse_npa_traffic_counts_accepts_split_and_full_municipality_codes() -> None:
    split = "都道府県コード,市区町村コード\n12,220\n12,220\n".encode()
    full = "市区町村コード\n12220\n".encode()

    assert parse_npa_traffic_counts(split) == {"12220": 2}
    assert parse_npa_traffic_counts(full) == {"12220": 1}


def test_land_survey_parser_uses_residential_code_year_and_municipality_code() -> None:
    region = RegionInfo(
        query="流山市",
        name="流山市",
        municipality_code="12220",
        prefecture="千葉県",
    )
    rows = parse_land_survey_prices(_land_survey_zip(), {"12220": region})

    assert len(rows) == 1
    assert rows[0].metric_code == "residential_land_price"
    assert rows[0].value == 65_000
    assert rows[0].sample_count == 2


def test_tertiary_school_parser_deduplicates_campuses_and_marks_unknown_status() -> None:
    region = RegionInfo(
        query="流山市", name="流山市", municipality_code="12220", prefecture="千葉県"
    )
    rows = parse_tertiary_education_campuses(_tertiary_school_zip(), {"12220": region})

    assert len(rows) == 1
    assert rows[0].metric_code == "tertiary_education_campuses"
    assert rows[0].axis == Axis.FAMILY
    assert rows[0].value == 2
    assert rows[0].sample_count == 2
    assert rows[0].source_id == TERTIARY_SCHOOL_SOURCE_ID
    assert "採点対象外" in (rows[0].note or "")


def test_housing_axis_uses_lower_quality_census_fallback() -> None:
    region = ResolvedRegion(code="01304", name="新篠津村", prefecture="北海道")
    points = [
        DashboardPoint(TOTAL_POPULATION, "01304", "2020CY00", 3_000),
        DashboardPoint(HOUSEHOLDS, "01304", "2020CY00", 1_200),
        DashboardPoint(HOUSEHOLDS_IN_HOUSING, "01304", "2020CY00", 1_140),
    ]

    _, rows = build_dashboard_metric_rows(region, points)
    housing = next(row for row in rows if row.metric_code == "housing_stock")

    assert housing.value == 950
    assert housing.quality == 0.65
    assert "代理指標" in (housing.note or "")
