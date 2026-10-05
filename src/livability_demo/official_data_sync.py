from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from time import monotonic
from urllib.parse import urlparse
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import httpx

from .models import Axis, RegionInfo
from .open_data import (
    OpenDataMetricRow,
    OpenDataSnapshot,
    OpenDataSource,
    load_open_data_snapshot,
    write_open_data_snapshot,
)

DASHBOARD_API_ROOT = "https://dashboard.e-stat.go.jp/api/1.0/Json"
DASHBOARD_SOURCE_ID = "ESTAT-DASHBOARD-API"
NPA_TRAFFIC_SOURCE_ID = "NPA-TRAFFIC-OPEN-DATA"
LAND_SURVEY_SOURCE_ID = "MLIT-KSJ-L02-2026"
LAND_SURVEY_DOWNLOAD_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/L02/L02-26/L02-26_GML.zip"
LAND_SURVEY_PAGE_URL = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-L02-2026.html"
LAND_SURVEY_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/deed.ja"
LAND_SURVEY_YEAR = 2026
TERTIARY_SCHOOL_SOURCE_ID = "MLIT-KSJ-P29-2023"
TERTIARY_SCHOOL_DOWNLOAD_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/P29/P29-23/P29-23_GML.zip"
TERTIARY_SCHOOL_PAGE_URL = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P29-2023.html"
TERTIARY_SCHOOL_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/deed.ja"
TERTIARY_SCHOOL_CATEGORIES = frozenset({"16005", "16006", "16007"})

TOTAL_POPULATION = "0201010000000010000"
CHILD_POPULATION = "0201010010000010010"
HOUSEHOLDS = "0202010000000010010"
RETAIL_ESTABLISHMENTS = "0601020200000010010"
DAYTIME_POPULATION_RATIO = "0201060000000020000"
HOUSING_COUNT = "0801010100000010000"
HOUSEHOLDS_IN_HOUSING = "0801100101000010010"
CLINICS_PER_100K = "1505020300000110010"
NURSERY_FACILITIES = "1503060101000010011"
ELEMENTARY_SCHOOLS = "1201010300000010000"
JUNIOR_HIGH_SCHOOLS = "1201010400000010000"
HIGH_SCHOOLS = "1201010500000010000"
FUTURE_TOTAL_POPULATION = "0201130020000010000"

ANNUAL_INDICATOR_GROUPS = (
    (
        TOTAL_POPULATION,
        CHILD_POPULATION,
        HOUSEHOLDS,
        RETAIL_ESTABLISHMENTS,
        DAYTIME_POPULATION_RATIO,
    ),
    (
        HOUSING_COUNT,
        NURSERY_FACILITIES,
        ELEMENTARY_SCHOOLS,
        JUNIOR_HIGH_SCHOOLS,
        HIGH_SCHOOLS,
    ),
    (FUTURE_TOTAL_POPULATION,),
)


@dataclass(frozen=True)
class DashboardPoint:
    indicator: str
    region_code: str
    time: str
    value: float

    @property
    def year(self) -> int | None:
        matched = re.search(r"(?:^|[^0-9])(\d{4})(?:[^0-9]|$)", self.time)
        return int(matched.group(1)) if matched else None


@dataclass(frozen=True)
class ResolvedRegion:
    code: str
    name: str
    prefecture: str | None


@dataclass(frozen=True)
class OfficialDataSyncResult:
    snapshot: OpenDataSnapshot
    refreshed_regions: tuple[RegionInfo, ...]
    refreshed_metric_count: int


def _api_result(payload: Mapping[str, object], root_key: str) -> Mapping[str, object]:
    root = payload.get(root_key)
    if not isinstance(root, Mapping):
        raise ValueError(f"統計ダッシュボードAPIの応答に{root_key}がありません")
    result = root.get("RESULT")
    if not isinstance(result, Mapping):
        raise ValueError("統計ダッシュボードAPIの応答にRESULTがありません")
    status = str(result.get("status", ""))
    if status != "0":
        message = result.get("errorMsg") or result.get("message") or "unknown error"
        raise ValueError(f"統計ダッシュボードAPIエラー status={status}: {message}")
    return root


def _extract_regions(root: Mapping[str, object]) -> list[ResolvedRegion]:
    raw_candidates: dict[str, str] = {}
    prefectures: dict[str, str] = {}

    def visit(node: object) -> None:
        if isinstance(node, Mapping):
            if "@parentRegionCode" in node and "@name" in node:
                parent_code = str(node["@parentRegionCode"]).strip()
                if len(parent_code) == 5 and parent_code.endswith("000"):
                    prefectures[parent_code[:2]] = str(node["@name"]).strip()
            if "@regionCode" in node and "@name" in node:
                code = str(node["@regionCode"]).strip()
                name = str(node["@name"]).strip()
                active_until = str(node.get("@toDate", "999912"))
                if code and name and active_until >= "999912":
                    raw_candidates[code] = name
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(root.get("METADATA_INF"))
    return [
        ResolvedRegion(
            code=code,
            name=name,
            prefecture=prefectures.get(code[:2]),
        )
        for code, name in sorted(raw_candidates.items())
    ]


class StatisticsDashboardClient:
    """Small, rate-limited client for the official no-registration API."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
        minimum_interval_seconds: float = 0.25,
        attempts: int = 3,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"User-Agent": "livability-agent-open-data-sync/1.0"},
            follow_redirects=False,
        )
        self._minimum_interval_seconds = max(0, minimum_interval_seconds)
        self._attempts = max(1, attempts)
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self.request_log: list[dict[str, object]] = []

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get_json(self, endpoint: str, params: Mapping[str, str]) -> dict[str, object]:
        url = f"{DASHBOARD_API_ROOT}/{endpoint}"
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            try:
                async with self._request_lock:
                    wait_seconds = self._minimum_interval_seconds - (
                        monotonic() - self._last_request_at
                    )
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    self._last_request_at = monotonic()
                    response = await self._client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("統計ダッシュボードAPIがJSON objectを返しませんでした")
                self.request_log.append(
                    {"endpoint": endpoint, "params": dict(params), "response": payload}
                )
                return payload
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self._attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
        assert last_error is not None
        raise ValueError(f"統計ダッシュボードAPIの取得に失敗しました: {last_error}") from last_error

    async def resolve_region(self, name: str) -> ResolvedRegion:
        payload = await self._get_json(
            "getRegionInfo",
            {"Lang": "JP", "SearchRegionWord": name, "RegionLevel": "4"},
        )
        root = _api_result(payload, "GET_META_REGION_INF")
        candidates = _extract_regions(root)
        exact = [region for region in candidates if region.name == name.strip()]
        if len(exact) == 1:
            return exact[0]
        if not exact:
            found = "、".join(sorted({region.name for region in candidates})[:8])
            raise ValueError(f"自治体「{name}」を解決できませんでした。API候補: {found or 'なし'}")
        raise ValueError(f"自治体「{name}」が複数見つかりました。都道府県別指定は未対応です")

    async def list_regions(self) -> list[ResolvedRegion]:
        payload = await self._get_json(
            "getRegionInfo",
            {"Lang": "JP", "RegionLevel": "4"},
        )
        root = _api_result(payload, "GET_META_REGION_INF")
        regions = _extract_regions(root)
        if not regions:
            raise ValueError("統計ダッシュボードAPIから現行地域を取得できませんでした")
        return regions

    async def fetch_indicators(
        self,
        *,
        region_code: str | None,
        indicator_codes: Sequence[str],
        cycle: str,
        time_from: str = "2000CY00",
        time_to: str = "2050CY00",
    ) -> list[DashboardPoint]:
        if not 1 <= len(indicator_codes) <= 5:
            raise ValueError("IndicatorCodeはAPI仕様に合わせて1〜5件で指定してください")
        params = {
            "Lang": "JP",
            "IndicatorCode": ",".join(indicator_codes),
            "TimeFrom": time_from,
            "TimeTo": time_to,
            "Cycle": cycle,
            "RegionalRank": "4",
            "IsSeasonalAdjustment": "1",
            "MetaGetFlg": "N",
            "SectionHeaderFlg": "1",
        }
        if region_code:
            params["RegionCode"] = region_code
        payload = await self._get_json("getData", params)
        root = _api_result(payload, "GET_STATS")
        points: list[DashboardPoint] = []

        def visit(node: object) -> None:
            if isinstance(node, Mapping):
                required = {"@indicator", "@regionCode", "@time", "$"}
                if required.issubset(node):
                    raw_value = str(node["$"]).replace(",", "").strip()
                    try:
                        value = float(raw_value)
                    except ValueError:
                        value = float("nan")
                    if value == value:
                        points.append(
                            DashboardPoint(
                                indicator=str(node["@indicator"]),
                                region_code=str(node["@regionCode"]),
                                time=str(node["@time"]),
                                value=value,
                            )
                        )
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(root.get("STATISTICAL_DATA"))
        requested = set(indicator_codes)
        return [
            point
            for point in points
            if (region_code is None or point.region_code == region_code)
            and point.indicator in requested
        ]


def _latest(points: Sequence[DashboardPoint]) -> DashboardPoint | None:
    with_year = [point for point in points if point.year is not None]
    return max(with_year, key=lambda point: (point.year or 0, point.time), default=None)


def _year(points: Sequence[DashboardPoint], year: int) -> DashboardPoint | None:
    matches = [point for point in points if point.year == year]
    return max(matches, key=lambda point: point.time, default=None)


def _nearest_year(
    points: Sequence[DashboardPoint], reference: DashboardPoint
) -> DashboardPoint | None:
    if reference.year is None:
        return _latest(points)
    with_year = [point for point in points if point.year is not None]
    return min(
        with_year,
        key=lambda point: (abs((point.year or 0) - reference.year), -(point.year or 0)),
        default=None,
    )


def _comparison_group(region_code: str, population: float) -> str:
    if region_code.startswith("131") and 13101 <= int(region_code) <= 13123:
        return "東京23区"
    if population >= 500_000:
        return "人口50万人以上の市"
    if population >= 200_000:
        return "人口20万～50万人の市"
    if population >= 50_000:
        return "人口5万～20万人の市"
    return "人口5万人未満の市区町村"


def _metric_row(
    region: RegionInfo,
    axis: Axis,
    metric_code: str,
    value: float,
    quality: float,
    note: str,
) -> OpenDataMetricRow:
    return OpenDataMetricRow(
        region=region,
        axis=axis,
        metric_code=metric_code,
        value=round(value, 2),
        quality=quality,
        source_id=DASHBOARD_SOURCE_ID,
        sample_count=None,
        note=note,
    )


def build_dashboard_metric_rows(
    resolved: ResolvedRegion,
    points: Sequence[DashboardPoint],
) -> tuple[RegionInfo, list[OpenDataMetricRow]]:
    by_indicator = {
        code: [point for point in points if point.indicator == code]
        for code in {
            TOTAL_POPULATION,
            CHILD_POPULATION,
            HOUSEHOLDS,
            RETAIL_ESTABLISHMENTS,
            DAYTIME_POPULATION_RATIO,
            HOUSING_COUNT,
            HOUSEHOLDS_IN_HOUSING,
            CLINICS_PER_100K,
            NURSERY_FACILITIES,
            ELEMENTARY_SCHOOLS,
            JUNIOR_HIGH_SCHOOLS,
            HIGH_SCHOOLS,
            FUTURE_TOTAL_POPULATION,
        }
    }
    base_population = _year(by_indicator[TOTAL_POPULATION], 2020) or _latest(
        by_indicator[TOTAL_POPULATION]
    )
    if base_population is None or base_population.value <= 0:
        raise ValueError(f"{resolved.name}の総人口が取得できないため指標を計算できません")

    region = RegionInfo(
        query=resolved.name,
        name=resolved.name,
        municipality_code=resolved.code,
        prefecture=resolved.prefecture,
        comparison_group=_comparison_group(resolved.code, base_population.value),
        is_mock_resolution=False,
    )
    rows: list[OpenDataMetricRow] = []

    retail = _latest(by_indicator[RETAIL_ESTABLISHMENTS])
    if retail and (population := _nearest_year(by_indicator[TOTAL_POPULATION], retail)):
        rows.append(
            _metric_row(
                region,
                Axis.CONVENIENCE,
                "retail_density",
                retail.value / population.value * 1_000,
                0.85,
                f"小売事業所数を人口で除した代理指標（事業所{retail.year}年、人口{population.year}年）",
            )
        )

    daytime = _latest(by_indicator[DAYTIME_POPULATION_RATIO])
    if daytime:
        rows.append(
            _metric_row(
                region,
                Axis.CONVENIENCE,
                "daytime_population_ratio",
                daytime.value,
                0.95,
                f"統計ダッシュボード掲載値（{daytime.year}年）",
            )
        )

    housing = _latest(by_indicator[HOUSING_COUNT])
    if housing and (households := _nearest_year(by_indicator[HOUSEHOLDS], housing)):
        rows.append(
            _metric_row(
                region,
                Axis.HOUSING,
                "housing_stock",
                housing.value / households.value * 1_000,
                0.9,
                f"住宅数を一般世帯数で除して算出（住宅{housing.year}年、世帯{households.year}年）",
            )
        )
    elif occupied := _latest(by_indicator[HOUSEHOLDS_IN_HOUSING]):
        households = _nearest_year(by_indicator[HOUSEHOLDS], occupied)
        if households and households.value > 0:
            rows.append(
                _metric_row(
                    region,
                    Axis.HOUSING,
                    "housing_stock",
                    occupied.value / households.value * 1_000,
                    0.65,
                    "住宅・土地統計調査の住宅数がない地域の代理指標。"
                    "住宅に住む一般世帯数を一般世帯数で除して算出"
                    f"（住宅居住世帯{occupied.year}年、一般世帯{households.year}年）",
                )
            )

    clinics = _latest(by_indicator[CLINICS_PER_100K])
    if clinics:
        rows.append(
            _metric_row(
                region,
                Axis.FAMILY,
                "clinics_per_100k",
                clinics.value,
                0.95,
                f"統計ダッシュボード掲載の人口10万人当たり一般診療所数（{clinics.year}年）",
            )
        )

    nursery = _latest(by_indicator[NURSERY_FACILITIES])
    if nursery and (children := _nearest_year(by_indicator[CHILD_POPULATION], nursery)):
        rows.append(
            _metric_row(
                region,
                Axis.FAMILY,
                "nursery_per_1000_children",
                nursery.value / children.value * 1_000,
                0.85,
                f"保育所等数を0～14歳人口で除した代理指標（施設{nursery.year}年、人口{children.year}年）",
            )
        )

    school_series = (
        by_indicator[ELEMENTARY_SCHOOLS],
        by_indicator[JUNIOR_HIGH_SCHOOLS],
        by_indicator[HIGH_SCHOOLS],
    )
    latest_schools = [_latest(series) for series in school_series]
    if all(latest_schools):
        common_year = min(point.year or 0 for point in latest_schools if point)
        school_points = [
            _year(series, common_year) or _nearest_year(series, latest_schools[index])
            for index, series in enumerate(school_series)
        ]
        child_reference = school_points[0]
        children = (
            _nearest_year(by_indicator[CHILD_POPULATION], child_reference)
            if child_reference
            else None
        )
        if children and all(school_points):
            school_count = sum(point.value for point in school_points if point)
            rows.append(
                _metric_row(
                    region,
                    Axis.FAMILY,
                    "schools_per_1000_children",
                    school_count / children.value * 1_000,
                    0.85,
                    "小・中・高等学校数を0～14歳人口で除した代理指標"
                    f"（学校{common_year}年、人口{children.year}年）",
                )
            )

    for target_year, metric_code, weight_quality in (
        (2040, "population_retention_2040", 0.92),
        (2050, "population_retention_2050", 0.92),
    ):
        future = _year(by_indicator[FUTURE_TOTAL_POPULATION], target_year)
        if future:
            rows.append(
                _metric_row(
                    region,
                    Axis.FUTURE,
                    metric_code,
                    future.value / base_population.value * 100,
                    weight_quality,
                    f"{target_year}年推計人口を{base_population.year}年実績人口で除して算出",
                )
            )

    return region, rows


def parse_npa_traffic_counts(payload: bytes) -> dict[str, int]:
    text: str | None = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("警察庁CSVの文字コードを判定できません")

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []

    def normalized(value: str) -> str:
        return re.sub(r"[\s\u3000]", "", value.lstrip("\ufeff"))

    normalized_fields = {normalized(field): field for field in fieldnames}
    prefecture_field = normalized_fields.get("都道府県コード")
    municipality_field = normalized_fields.get("市区町村コード")
    if municipality_field is None:
        raise ValueError("警察庁CSVに市区町村コード列がありません")

    counts: dict[str, int] = {}
    for row in reader:
        municipality = re.sub(r"\D", "", row.get(municipality_field, ""))
        prefecture = re.sub(r"\D", "", row.get(prefecture_field, "")) if prefecture_field else ""
        if len(municipality) == 5:
            code = municipality
        elif len(municipality) <= 3 and municipality and prefecture:
            code = f"{prefecture.zfill(2)}{municipality.zfill(3)}"
        elif municipality:
            code = municipality.zfill(5)
        else:
            continue
        counts[code] = counts.get(code, 0) + 1
    return counts


def parse_land_survey_prices(
    payload: bytes,
    regions: Mapping[str, RegionInfo],
) -> list[OpenDataMetricRow]:
    """Extract city-level 2026 residential land-price medians from the official KSJ GeoJSON."""

    if not payload or len(payload) > 128 * 1024 * 1024:
        raise ValueError("国土数値情報L02 ZIPのサイズが許容範囲外です")
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            members = [
                member
                for member in archive.infolist()
                if member.filename.lower().endswith(".geojson")
            ]
            if not members or sum(member.file_size for member in members) > 160 * 1024 * 1024:
                raise ValueError("国土数値情報L02 ZIPに有効なGeoJSONがありません")
            prices_by_region: dict[str, list[float]] = {}
            feature_count = 0
            for member in members:
                with archive.open(member) as source:
                    data = json.load(source)
                features = data.get("features") if isinstance(data, dict) else None
                if not isinstance(features, list):
                    raise ValueError("国土数値情報L02 GeoJSONのfeaturesが不正です")
                for feature in features:
                    feature_count += 1
                    if not isinstance(feature, Mapping):
                        continue
                    properties = feature.get("properties")
                    if not isinstance(properties, Mapping):
                        continue
                    code = str(properties.get("L02_020", "")).strip()
                    year = properties.get("L02_005")
                    use_code = str(properties.get("L02_001", "")).strip().zfill(3)
                    if code not in regions or year != LAND_SURVEY_YEAR or use_code != "000":
                        continue
                    try:
                        value = float(properties.get("L02_006"))
                    except (TypeError, ValueError):
                        continue
                    if value > 0 and value == value:
                        prices_by_region.setdefault(code, []).append(value)
            if feature_count < 1_000:
                raise ValueError("国土数値情報L02 GeoJSONの地物数が少なすぎます")
    except BadZipFile as exc:
        raise ValueError("国土数値情報L02が有効なZIPではありません") from exc

    rows: list[OpenDataMetricRow] = []
    for code, prices in prices_by_region.items():
        region = regions[code]
        rows.append(
            OpenDataMetricRow(
                region=region,
                axis=Axis.HOUSING,
                metric_code="residential_land_price",
                value=round(median(prices), 2),
                quality=min(0.92, 0.55 + min(len(prices), 37) * 0.01),
                source_id=LAND_SURVEY_SOURCE_ID,
                sample_count=len(prices),
                note=(
                    f"2026年7月1日時点の住宅地基準地点{len(prices)}件の調査価格中央値。"
                    "単位は円/㎡。家賃や売買成約価格ではない"
                ),
            )
        )
    return rows


def parse_tertiary_education_campuses(
    payload: bytes,
    regions: Mapping[str, RegionInfo],
) -> list[OpenDataMetricRow]:
    """Count published tertiary school campuses by the dataset's municipality code."""

    if not payload or len(payload) > 128 * 1024 * 1024:
        raise ValueError("国土数値情報P29 ZIPのサイズが許容範囲外です")
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            members = [
                member
                for member in archive.infolist()
                if member.filename.lower().endswith(".geojson")
            ]
            if not members or sum(member.file_size for member in members) > 320 * 1024 * 1024:
                raise ValueError("国土数値情報P29 ZIPに有効なGeoJSONがありません")
            campuses_by_region: dict[str, set[tuple[str, str]]] = {code: set() for code in regions}
            unverified_by_region: dict[str, set[tuple[str, str]]] = {
                code: set() for code in regions
            }
            feature_count = 0
            for member in members:
                with archive.open(member) as source:
                    data = json.load(source)
                features = data.get("features") if isinstance(data, dict) else None
                if not isinstance(features, list):
                    raise ValueError("国土数値情報P29 GeoJSONのfeaturesが不正です")
                for feature in features:
                    feature_count += 1
                    if not isinstance(feature, Mapping):
                        continue
                    properties = feature.get("properties")
                    if not isinstance(properties, Mapping):
                        continue
                    code = str(properties.get("P29_001", "")).strip()
                    category = str(properties.get("P29_003", "")).strip()
                    school_code = str(properties.get("P29_002", "")).strip()
                    campus_code = str(properties.get("P29_008", "")).strip()
                    status = str(properties.get("P29_007", "")).strip()
                    if (
                        code not in regions
                        or category not in TERTIARY_SCHOOL_CATEGORIES
                        or not school_code
                        or not campus_code
                        or status not in {"0", "1", "2"}
                        or status == "2"
                    ):
                        continue
                    campus = (school_code, campus_code)
                    campuses_by_region[code].add(campus)
                    if status == "0":
                        unverified_by_region[code].add(campus)
            if feature_count < 1_000:
                raise ValueError("国土数値情報P29 GeoJSONの地物数が少なすぎます")
    except BadZipFile as exc:
        raise ValueError("国土数値情報P29が有効なZIPではありません") from exc

    rows: list[OpenDataMetricRow] = []
    for code, region in regions.items():
        campuses = campuses_by_region[code]
        unverified_count = len(unverified_by_region[code])
        if campuses:
            status_note = (
                f"うち状態コード0（調査なし）が{unverified_count}件あり現況未確認。"
                if unverified_count
                else "掲載状態コード1のキャンパスを集計。"
            )
        else:
            status_note = "データ内に該当掲載なし。実態や近隣自治体の施設を示すものではない。"
        rows.append(
            OpenDataMetricRow(
                region=region,
                axis=Axis.FAMILY,
                metric_code="tertiary_education_campuses",
                value=float(len(campuses)),
                quality=0.45,
                source_id=TERTIARY_SCHOOL_SOURCE_ID,
                sample_count=len(campuses),
                note=(
                    "2023年版学校データの大学・短大・高専（分類16005/16006/16007）を、"
                    "学校コードとキャンパスコードの組で重複排除。閉校コード2を除外。"
                    f"{status_note}定員・学部・通学時間は分からず、掲載数は採点対象外。"
                ),
            )
        )
    return rows


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def _download_official_file(
    client: httpx.AsyncClient,
    url: str,
    *,
    expected_sha256: str | None,
) -> bytes:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not (hostname == "npa.go.jp" or hostname.endswith(".npa.go.jp")):
        raise ValueError("交通事故CSVは警察庁のHTTPS URLだけを許可します")
    response = await client.get(url, follow_redirects=False)
    response.raise_for_status()
    payload = response.content
    if not payload or len(payload) > 128 * 1024 * 1024:
        raise ValueError("警察庁CSVのサイズが許容範囲外です")
    actual_sha256 = _sha256_bytes(payload)
    if expected_sha256 and actual_sha256 != expected_sha256.lower():
        raise ValueError("警察庁CSVが設定済みSHA-256と一致しません")
    return payload


async def _download_land_survey_file(client: httpx.AsyncClient) -> bytes:
    parsed = urlparse(LAND_SURVEY_DOWNLOAD_URL)
    if parsed.scheme != "https" or parsed.hostname != "nlftp.mlit.go.jp":
        raise ValueError("地価調査ZIPは国土数値情報のHTTPS URLだけを許可します")
    response = await client.get(LAND_SURVEY_DOWNLOAD_URL, follow_redirects=False)
    response.raise_for_status()
    payload = response.content
    if not payload or len(payload) > 128 * 1024 * 1024:
        raise ValueError("国土数値情報L02 ZIPのサイズが許容範囲外です")
    return payload


async def _download_tertiary_school_file(client: httpx.AsyncClient) -> bytes:
    parsed = urlparse(TERTIARY_SCHOOL_DOWNLOAD_URL)
    if parsed.scheme != "https" or parsed.hostname != "nlftp.mlit.go.jp":
        raise ValueError("学校データZIPは国土数値情報のHTTPS URLだけを許可します")
    response = await client.get(TERTIARY_SCHOOL_DOWNLOAD_URL, follow_redirects=False)
    response.raise_for_status()
    payload = response.content
    if not payload or len(payload) > 128 * 1024 * 1024:
        raise ValueError("国土数値情報P29 ZIPのサイズが許容範囲外です")
    return payload


def _dashboard_source(
    payload: bytes,
    retrieved_at: date,
    *,
    all_regions: bool,
) -> OpenDataSource:
    payload_sha256 = _sha256_bytes(payload)
    return OpenDataSource(
        source_id=f"{DASHBOARD_SOURCE_ID}-{payload_sha256[:12]}",
        source_name="統計ダッシュボード API",
        publisher="総務省統計局",
        download_page_url="https://dashboard.e-stat.go.jp/static/api",
        license_id="PDL1.0",
        license_url="https://dashboard.e-stat.go.jp/static/terms",
        attribution=(
            "出典：統計ダッシュボード。統計ダッシュボードのデータを加工して作成。"
            "このサービスは、統計ダッシュボードのAPI機能を使用していますが、"
            "サービスの内容は国によって保証されたものではありません。"
        ),
        retrieved_at=retrieved_at,
        reference_date="各指標の基準年はmetrics.csvのnoteに記録",
        original_filename=(
            f"statistics-dashboard-api-{retrieved_at.isoformat()}-{payload_sha256[:12]}.json"
        ),
        original_sha256=payload_sha256,
        commercial_use_note="統計ダッシュボードのPDL1.0と出典・加工表示条件に従い二次利用",
        third_party_rights_note="API取得値のみ使用。第三者が権利を有する表示物は未使用",
        transformation_note=("取得範囲=全国市区町村。" if all_regions else "取得範囲=指定自治体。")
        + "自治体コードで抽出し、比率指標はnote記載の分子・分母から算出",
    )


def _traffic_source(
    payload: bytes,
    retrieved_at: date,
    reference_year: int,
    source_url: str,
) -> OpenDataSource:
    payload_sha256 = _sha256_bytes(payload)
    upstream_name = Path(urlparse(source_url).path).name
    upstream_path = Path(upstream_name)
    return OpenDataSource(
        source_id=f"{NPA_TRAFFIC_SOURCE_ID}-{payload_sha256[:12]}",
        source_name="交通事故統計情報のオープンデータ（本票）",
        publisher="警察庁",
        download_page_url=(
            "https://www.npa.go.jp/publications/statistics/koutsuu/opendata/index_opendata.html"
        ),
        license_id="PDL1.0",
        license_url="https://www.npa.go.jp/rules/index.html",
        attribution="出典：警察庁ウェブサイト「交通事故統計情報のオープンデータ」",
        retrieved_at=retrieved_at,
        reference_date=str(reference_year),
        original_filename=(
            f"{upstream_path.stem}-{payload_sha256[:12]}{upstream_path.suffix or '.csv'}"
        ),
        original_sha256=payload_sha256,
        commercial_use_note="警察庁ウェブサイト利用規約と公共データ利用規約に従い利用",
        third_party_rights_note="CSV本票の数値・コードのみ使用し、第三者権利表示物は未使用",
        transformation_note="都道府県コードと市区町村コードで事故レコードを集計し人口で除した",
    )


def _land_survey_source(payload: bytes, retrieved_at: date) -> OpenDataSource:
    payload_sha256 = _sha256_bytes(payload)
    return OpenDataSource(
        source_id=f"{LAND_SURVEY_SOURCE_ID}-{payload_sha256[:12]}",
        source_name="国土数値情報 都道府県地価調査（2026年）",
        publisher="国土交通省",
        download_page_url=LAND_SURVEY_PAGE_URL,
        license_id="CC-BY-4.0",
        license_url=LAND_SURVEY_LICENSE_URL,
        attribution=(
            f"出典：国土交通省 国土数値情報 都道府県地価調査（2026年）"
            f"（{LAND_SURVEY_PAGE_URL}）を加工して作成"
        ),
        retrieved_at=retrieved_at,
        reference_date="2026-07-01",
        original_filename=f"L02-26-{payload_sha256[:12]}_GML.zip",
        original_sha256=payload_sha256,
        commercial_use_note="対象版の個別ページで2018年以降のデータにCC BY 4.0を確認",
        third_party_rights_note="対象版の個別ページに記載された利用条件に従う",
        transformation_note=(
            "同梱GeoJSONから用途区分000（住宅地）、年度2026、行政区域コード"
            "L02_020が地域一覧に一致する地点を抽出。円/㎡の基準地価格中央値を算出。"
            "コード不明の00000等は除外。"
        ),
    )


def _tertiary_school_source(payload: bytes, retrieved_at: date) -> OpenDataSource:
    payload_sha256 = _sha256_bytes(payload)
    return OpenDataSource(
        source_id=f"{TERTIARY_SCHOOL_SOURCE_ID}-{payload_sha256[:12]}",
        source_name="国土数値情報 学校（2023年）",
        publisher="国土交通省",
        download_page_url=TERTIARY_SCHOOL_PAGE_URL,
        license_id="CC-BY-4.0",
        license_url=TERTIARY_SCHOOL_LICENSE_URL,
        attribution=(
            f"出典：国土交通省 国土数値情報 学校（2023年）"
            f"（{TERTIARY_SCHOOL_PAGE_URL}）を加工して作成"
        ),
        retrieved_at=retrieved_at,
        reference_date="2023",
        original_filename=f"P29-23-{payload_sha256[:12]}_GML.zip",
        original_sha256=payload_sha256,
        commercial_use_note="対象版の個別ページでCC BY 4.0を確認",
        third_party_rights_note="対象版の個別ページに記載された利用条件に従う",
        transformation_note=(
            "学校分類16005/16006/16007を自治体コードP29_001で抽出し、"
            "学校コードP29_002とキャンパスコードP29_008の組で重複排除。"
            "閉校コード2を除外。状態コード0（調査なし）は現況不明として注記。"
            "定員・学部・通学時間は推定せず、参考情報として保存。"
        ),
    )


def _load_existing_snapshot(output_dir: Path) -> OpenDataSnapshot | None:
    if not (output_dir.expanduser().resolve() / "manifest.json").is_file():
        return None
    return load_open_data_snapshot(output_dir)


async def sync_official_open_data(
    *,
    region_names: Sequence[str],
    output_dir: Path,
    traffic_csv_url: str,
    traffic_reference_year: int,
    traffic_expected_sha256: str | None = None,
    include_traffic: bool = True,
    all_regions: bool = False,
    timeout_seconds: float = 60,
    minimum_interval_seconds: float = 0.25,
    http_client: httpx.AsyncClient | None = None,
) -> OfficialDataSyncResult:
    """Fetch approved no-account sources, merge existing rows, and publish a snapshot."""

    requested_names = tuple(dict.fromkeys(name.strip() for name in region_names if name.strip()))
    if not all_regions and not requested_names:
        raise ValueError("同期対象の自治体名を1件以上指定してください")

    target_root = await asyncio.to_thread(lambda: output_dir.expanduser().resolve())
    existing = await asyncio.to_thread(_load_existing_snapshot, target_root)
    owns_http_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=timeout_seconds,
        headers={"User-Agent": "livability-agent-open-data-sync/1.0"},
        follow_redirects=False,
    )
    dashboard = StatisticsDashboardClient(
        http_client=client,
        timeout_seconds=timeout_seconds,
        minimum_interval_seconds=minimum_interval_seconds,
    )
    refreshed_regions: list[RegionInfo] = []
    fresh_rows: list[OpenDataMetricRow] = []
    population_by_code: dict[str, float] = {}
    traffic_payload: bytes | None = None
    land_survey_payload: bytes | None = None
    tertiary_school_payload: bytes | None = None
    try:
        if all_regions:
            resolved_regions = await dashboard.list_regions()
            nationwide_points: list[DashboardPoint] = []
            current_indicators = tuple(
                indicator for group in ANNUAL_INDICATOR_GROUPS[:2] for indicator in group
            ) + (HOUSEHOLDS_IN_HOUSING,)
            for offset in range(0, len(current_indicators), 2):
                nationwide_points.extend(
                    await dashboard.fetch_indicators(
                        region_code=None,
                        indicator_codes=current_indicators[offset : offset + 2],
                        cycle="3",
                        time_from="2018CY00",
                        time_to="2025CY00",
                    )
                )
            nationwide_points.extend(
                await dashboard.fetch_indicators(
                    region_code=None,
                    indicator_codes=ANNUAL_INDICATOR_GROUPS[2],
                    cycle="3",
                    time_from="2025CY00",
                    time_to="2050CY00",
                )
            )
            nationwide_points.extend(
                await dashboard.fetch_indicators(
                    region_code=None,
                    indicator_codes=(CLINICS_PER_100K,),
                    cycle="4",
                    time_from="2018FY00",
                    time_to="2025FY00",
                )
            )
            points_by_region: dict[str, list[DashboardPoint]] = {}
            for point in nationwide_points:
                points_by_region.setdefault(point.region_code, []).append(point)
            resolved_queue = [
                (resolved, points_by_region.get(resolved.code, []))
                for resolved in resolved_regions
                if resolved.code in points_by_region
            ]
        else:
            resolved_queue = []
            for name in requested_names:
                resolved = await dashboard.resolve_region(name)
                points: list[DashboardPoint] = []
                for indicators in ANNUAL_INDICATOR_GROUPS:
                    points.extend(
                        await dashboard.fetch_indicators(
                            region_code=resolved.code,
                            indicator_codes=indicators,
                            cycle="3",
                        )
                    )
                points.extend(
                    await dashboard.fetch_indicators(
                        region_code=resolved.code,
                        indicator_codes=(CLINICS_PER_100K,),
                        cycle="4",
                        time_from="2000FY00",
                        time_to="2050FY00",
                    )
                )
                points.extend(
                    await dashboard.fetch_indicators(
                        region_code=resolved.code,
                        indicator_codes=(HOUSEHOLDS_IN_HOUSING,),
                        cycle="3",
                    )
                )
                resolved_queue.append((resolved, points))

        for resolved, points in resolved_queue:
            population_points = [point for point in points if point.indicator == TOTAL_POPULATION]
            population = _year(population_points, 2020) or _latest(population_points)
            if population is None or population.value <= 0:
                if all_regions:
                    continue
                raise ValueError(f"{resolved.name}の2020年人口を取得できません")
            region, rows = build_dashboard_metric_rows(resolved, points)
            population_by_code[resolved.code] = population.value
            refreshed_regions.append(region)
            fresh_rows.extend(rows)

        land_survey_payload = await _download_land_survey_file(client)
        fresh_rows.extend(
            parse_land_survey_prices(
                land_survey_payload,
                {region.municipality_code: region for region in refreshed_regions},
            )
        )
        tertiary_school_payload = await _download_tertiary_school_file(client)
        fresh_rows.extend(
            parse_tertiary_education_campuses(
                tertiary_school_payload,
                {region.municipality_code: region for region in refreshed_regions},
            )
        )

        if include_traffic:
            traffic_payload = await _download_official_file(
                client,
                traffic_csv_url,
                expected_sha256=traffic_expected_sha256,
            )
            counts = parse_npa_traffic_counts(traffic_payload)
            for region in refreshed_regions:
                population = population_by_code.get(region.municipality_code)
                if population is None or population <= 0:
                    raise ValueError(f"{region.name}の交通事故率の人口分母を取得できません")
                accident_count = counts.get(region.municipality_code)
                if accident_count is None:
                    # No matching code is not evidence of zero accidents.
                    continue
                fresh_rows.append(
                    OpenDataMetricRow(
                        region=region,
                        axis=Axis.SAFETY,
                        metric_code="traffic_accidents",
                        value=round(accident_count / population * 100_000, 2),
                        quality=0.9,
                        source_id=NPA_TRAFFIC_SOURCE_ID,
                        sample_count=accident_count,
                        note=(
                            f"警察庁本票{traffic_reference_year}年の事故{accident_count}件を"
                            "統計ダッシュボードの2020年人口で除して算出"
                        ),
                    )
                )
    finally:
        await dashboard.close()
        if owns_http_client:
            await client.aclose()

    if not fresh_rows:
        raise ValueError("公式ソースからLivability指標を1件も作成できませんでした")

    retrieved_at = datetime.now(UTC).date()
    dashboard_payload = json.dumps(
        {
            "retrieved_at": retrieved_at.isoformat(),
            "requests": dashboard.request_log,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fresh_sources = [_dashboard_source(dashboard_payload, retrieved_at, all_regions=all_regions)]
    _atomic_write(target_root / fresh_sources[0].original_filename, dashboard_payload)
    source_id_replacements = {
        DASHBOARD_SOURCE_ID: fresh_sources[0].source_id,
    }
    if traffic_payload is not None:
        traffic_source = _traffic_source(
            traffic_payload,
            retrieved_at,
            traffic_reference_year,
            traffic_csv_url,
        )
        fresh_sources.append(traffic_source)
        _atomic_write(target_root / traffic_source.original_filename, traffic_payload)
        source_id_replacements[NPA_TRAFFIC_SOURCE_ID] = traffic_source.source_id
    if land_survey_payload is not None:
        land_survey_source = _land_survey_source(land_survey_payload, retrieved_at)
        fresh_sources.append(land_survey_source)
        _atomic_write(target_root / land_survey_source.original_filename, land_survey_payload)
        source_id_replacements[LAND_SURVEY_SOURCE_ID] = land_survey_source.source_id
    if tertiary_school_payload is not None:
        tertiary_school_source = _tertiary_school_source(tertiary_school_payload, retrieved_at)
        fresh_sources.append(tertiary_school_source)
        _atomic_write(
            target_root / tertiary_school_source.original_filename,
            tertiary_school_payload,
        )
        source_id_replacements[TERTIARY_SCHOOL_SOURCE_ID] = tertiary_school_source.source_id
    fresh_rows = [
        replace(row, source_id=source_id_replacements.get(row.source_id, row.source_id))
        for row in fresh_rows
    ]

    refreshed_codes = {region.municipality_code for region in refreshed_regions}
    refreshed_region_by_code = {region.municipality_code: region for region in refreshed_regions}
    refreshed_source_families = tuple(source_id_replacements)
    merged_rows: list[OpenDataMetricRow] = []
    for row in existing.rows if existing else ():
        if row.region.municipality_code in refreshed_codes and any(
            row.source_id == family or row.source_id.startswith(f"{family}-")
            for family in refreshed_source_families
        ):
            continue
        refreshed_region = refreshed_region_by_code.get(row.region.municipality_code)
        merged_rows.append(replace(row, region=refreshed_region) if refreshed_region else row)
    row_index = {(row.region.municipality_code, row.metric_code): row for row in merged_rows}
    for row in fresh_rows:
        row_index[(row.region.municipality_code, row.metric_code)] = row

    source_index = dict(existing.sources if existing else {})
    source_index.update({source.source_id: source for source in fresh_sources})
    used_source_ids = {row.source_id for row in row_index.values()}
    merged_sources = [
        source_index[source_id]
        for source_id in sorted(used_source_ids)
        if source_id in source_index
    ]
    snapshot = write_open_data_snapshot(
        rows=list(row_index.values()),
        sources=merged_sources,
        output_dir=target_root,
        force=True,
    )
    return OfficialDataSyncResult(
        snapshot=snapshot,
        refreshed_regions=tuple(refreshed_regions),
        refreshed_metric_count=len(fresh_rows),
    )
