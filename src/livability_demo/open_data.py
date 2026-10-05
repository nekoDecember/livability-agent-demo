from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import METRIC_CATALOG, MetricSpec
from .models import Axis, RegionInfo

APPROVED_LICENSES = frozenset(
    {
        "CC-BY-4.0",
        "PDL1.0",
        "COMMERCIAL-USE-ALLOWED",
    }
)
OFFICIAL_HOST_SUFFIXES = ("go.jp", "lg.jp")
LICENSE_HOSTS = frozenset({"creativecommons.org"})
METRICS_FIELDNAMES = (
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
)
METRICS_COLUMNS = frozenset(METRICS_FIELDNAMES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_official_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname:
        raise ValueError("download_page_url must be an HTTPS URL")
    if not any(
        hostname == suffix or hostname.endswith(f".{suffix}") for suffix in OFFICIAL_HOST_SUFFIXES
    ):
        raise ValueError(
            "download_page_url must use an official .go.jp or .lg.jp host; "
            "add new authorities through a reviewed code change"
        )
    return value


def _validate_license_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname:
        raise ValueError("license_url must use HTTPS")
    is_official = any(
        hostname == suffix or hostname.endswith(f".{suffix}") for suffix in OFFICIAL_HOST_SUFFIXES
    )
    is_license_authority = any(
        hostname == authority or hostname.endswith(f".{authority}") for authority in LICENSE_HOSTS
    )
    if not is_official and not is_license_authority:
        raise ValueError(
            "license_url must use an official government or approved license-authority host"
        )
    return value


class OpenDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=120)
    source_name: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=300)
    download_page_url: str
    license_id: str
    license_url: str
    attribution: str = Field(min_length=1, max_length=1_000)
    retrieved_at: date
    reference_date: str = Field(min_length=1, max_length=120)
    original_filename: str = Field(min_length=1, max_length=300)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    commercial_use_note: str = Field(min_length=1, max_length=1_000)
    third_party_rights_note: str = Field(min_length=1, max_length=1_000)
    transformation_note: str = Field(min_length=1, max_length=2_000)

    @field_validator("download_page_url")
    @classmethod
    def validate_download_page_url(cls, value: str) -> str:
        return _validate_official_url(value)

    @field_validator("license_url")
    @classmethod
    def validate_license_url(cls, value: str) -> str:
        return _validate_license_url(value)

    @field_validator("license_id")
    @classmethod
    def validate_license(cls, value: str) -> str:
        if value not in APPROVED_LICENSES:
            approved = ", ".join(sorted(APPROVED_LICENSES))
            raise ValueError(f"license_id must be one of: {approved}")
        return value

    @field_validator("original_filename")
    @classmethod
    def validate_original_filename(cls, value: str) -> str:
        if Path(value).name != value:
            raise ValueError("original_filename must not contain a directory")
        return value


class OpenDataManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    generated_at: datetime
    metrics_file: str = Field(min_length=1, max_length=120)
    metrics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: list[OpenDataSource] = Field(min_length=1)

    @field_validator("metrics_file")
    @classmethod
    def validate_metrics_file(cls, value: str) -> str:
        if Path(value).name != value or not value.lower().endswith(".csv"):
            raise ValueError("metrics_file must be a CSV filename without a directory")
        return value

    @model_validator(mode="after")
    def validate_unique_source_ids(self) -> OpenDataManifest:
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sources must have unique source_id values")
        return self


class OpenDataSourceInput(BaseModel):
    """Source metadata used only while building a validated snapshot."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=120)
    source_name: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=300)
    download_page_url: str
    license_id: str
    license_url: str
    attribution: str = Field(min_length=1, max_length=1_000)
    retrieved_at: date
    reference_date: str = Field(min_length=1, max_length=120)
    original_file: Path
    commercial_use_note: str = Field(min_length=1, max_length=1_000)
    third_party_rights_note: str = Field(min_length=1, max_length=1_000)
    transformation_note: str = Field(min_length=1, max_length=2_000)

    @field_validator("download_page_url")
    @classmethod
    def validate_download_page_url(cls, value: str) -> str:
        return _validate_official_url(value)

    @field_validator("license_url")
    @classmethod
    def validate_license_url(cls, value: str) -> str:
        return _validate_license_url(value)

    @field_validator("license_id")
    @classmethod
    def validate_license(cls, value: str) -> str:
        if value not in APPROVED_LICENSES:
            approved = ", ".join(sorted(APPROVED_LICENSES))
            raise ValueError(f"license_id must be one of: {approved}")
        return value


@dataclass(frozen=True)
class OpenDataMetricRow:
    region: RegionInfo
    axis: Axis
    metric_code: str
    value: float
    quality: float
    source_id: str
    sample_count: int | None
    note: str | None


@dataclass(frozen=True)
class OpenDataSnapshot:
    root: Path
    manifest: OpenDataManifest
    sources: dict[str, OpenDataSource]
    rows: tuple[OpenDataMetricRow, ...]
    regions: dict[str, RegionInfo]

    def resolve_region(self, user_request: str) -> RegionInfo:
        code_match = re.search(r"対象自治体コード\s*[:：]\s*(\d{5})", user_request)
        if code_match:
            region = self.regions.get(code_match.group(1))
            if region is None:
                raise ValueError("選択した自治体コードは現在の公式公開データに収録されていません。")
            return region
        matches = [region for region in self.regions.values() if region.name in user_request]
        if len(matches) > 1:
            matches = [
                region
                for region in matches
                if region.prefecture and region.prefecture in user_request
            ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = "、".join(sorted({region.name for region in self.regions.values()})[:12])
            raise ValueError(
                "公式公開データのスナップショットに対象自治体がありません。"
                f"収録済み: {available or 'なし'}"
            )
        raise ValueError("同名自治体が複数あります。都道府県名も指定してください。")

    def rows_for(self, municipality_code: str, axis: Axis) -> dict[str, OpenDataMetricRow]:
        return {
            row.metric_code: row
            for row in self.rows
            if row.region.municipality_code == municipality_code and row.axis == axis
        }


def _metric_index() -> dict[str, tuple[Axis, MetricSpec]]:
    return {spec.code: (axis, spec) for axis, specs in METRIC_CATALOG.items() for spec in specs}


def normalized_score(value: float, spec: MetricSpec) -> float:
    """Apply the existing catalog range; snapshots contain raw values, not opaque scores."""

    if spec.raw_max <= spec.raw_min:
        raise ValueError(f"Invalid catalog range for metric={spec.code}")
    position = min(1.0, max(0.0, (value - spec.raw_min) / (spec.raw_max - spec.raw_min)))
    if spec.direction == "lower_is_better":
        position = 1 - position
    elif spec.direction == "context_only":
        return 50.0
    return round(position * 100, 1)


def _parse_required_text(row: dict[str, str | None], key: str, line_number: int) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError(f"metrics.csv line {line_number}: {key} is required")
    return value


def _read_metrics(
    path: Path,
    manifest: OpenDataManifest,
) -> tuple[tuple[OpenDataMetricRow, ...], dict[str, RegionInfo]]:
    sources = {source.source_id: source for source in manifest.sources}
    metric_index = _metric_index()
    parsed_rows: list[OpenDataMetricRow] = []
    regions: dict[str, RegionInfo] = {}
    identities: dict[str, tuple[str, str | None, str]] = {}
    seen: set[tuple[str, str]] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = frozenset(reader.fieldnames or ())
        if fieldnames != METRICS_COLUMNS:
            missing = sorted(METRICS_COLUMNS - fieldnames)
            extra = sorted(fieldnames - METRICS_COLUMNS)
            raise ValueError(
                f"metrics.csv columns do not match schema; missing={missing}, extra={extra}"
            )

        for line_number, raw in enumerate(reader, start=2):
            region_code = _parse_required_text(raw, "region_code", line_number)
            region_name = _parse_required_text(raw, "region_name", line_number)
            prefecture = (raw.get("prefecture") or "").strip() or None
            comparison_group = _parse_required_text(raw, "comparison_group", line_number)
            metric_code = _parse_required_text(raw, "metric_code", line_number)
            source_id = _parse_required_text(raw, "source_id", line_number)

            try:
                axis = Axis(_parse_required_text(raw, "axis", line_number))
            except ValueError as exc:
                raise ValueError(f"metrics.csv line {line_number}: invalid axis") from exc
            indexed = metric_index.get(metric_code)
            if indexed is None:
                raise ValueError(
                    f"metrics.csv line {line_number}: unknown metric_code={metric_code}"
                )
            expected_axis, _ = indexed
            if axis != expected_axis:
                raise ValueError(
                    f"metrics.csv line {line_number}: metric_code={metric_code} belongs to "
                    f"axis={expected_axis.value}"
                )
            if source_id not in sources:
                raise ValueError(f"metrics.csv line {line_number}: unknown source_id={source_id}")

            try:
                value = float(_parse_required_text(raw, "value", line_number))
                quality = float(_parse_required_text(raw, "quality", line_number))
            except ValueError as exc:
                raise ValueError(
                    f"metrics.csv line {line_number}: value and quality must be numbers"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(f"metrics.csv line {line_number}: value must be finite")
            if not math.isfinite(quality) or not 0 < quality <= 1:
                raise ValueError(
                    f"metrics.csv line {line_number}: quality must be greater than 0 and at most 1"
                )

            sample_text = (raw.get("sample_count") or "").strip()
            try:
                sample_count = int(sample_text) if sample_text else None
            except ValueError as exc:
                raise ValueError(
                    f"metrics.csv line {line_number}: sample_count must be an integer"
                ) from exc
            if sample_count is not None and sample_count < 0:
                raise ValueError(
                    f"metrics.csv line {line_number}: sample_count must not be negative"
                )

            identity = (region_name, prefecture, comparison_group)
            previous_identity = identities.setdefault(region_code, identity)
            if previous_identity != identity:
                raise ValueError(
                    f"metrics.csv line {line_number}: inconsistent region metadata for "
                    f"region_code={region_code}"
                )
            unique_key = (region_code, metric_code)
            if unique_key in seen:
                raise ValueError(
                    f"metrics.csv line {line_number}: duplicate region/metric pair {unique_key}"
                )
            seen.add(unique_key)

            region = regions.setdefault(
                region_code,
                RegionInfo(
                    query=region_name,
                    name=region_name,
                    municipality_code=region_code,
                    prefecture=prefecture,
                    comparison_group=comparison_group,
                    is_mock_resolution=False,
                ),
            )
            parsed_rows.append(
                OpenDataMetricRow(
                    region=region,
                    axis=axis,
                    metric_code=metric_code,
                    value=value,
                    quality=quality,
                    source_id=source_id,
                    sample_count=sample_count,
                    note=(raw.get("note") or "").strip() or None,
                )
            )

    if not parsed_rows:
        raise ValueError("metrics.csv must contain at least one data row")
    return tuple(parsed_rows), regions


def load_open_data_snapshot(root: Path) -> OpenDataSnapshot:
    snapshot_root = root.expanduser().resolve()
    manifest_path = snapshot_root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"公式公開データのmanifest.jsonがありません: {manifest_path}")
    try:
        manifest = OpenDataManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"公式公開データのmanifest.jsonが不正です: {exc}") from exc

    metrics_path = snapshot_root / manifest.metrics_file
    if not metrics_path.is_file():
        raise ValueError(f"公式公開データのmetrics CSVがありません: {metrics_path}")
    actual_hash = sha256_file(metrics_path)
    if actual_hash != manifest.metrics_sha256:
        raise ValueError(
            "公式公開データのmetrics CSVがmanifest作成後に変更されています（SHA-256不一致）"
        )
    for source in manifest.sources:
        original_path = snapshot_root / source.original_filename
        if original_path.is_file() and sha256_file(original_path) != source.original_sha256:
            raise ValueError(
                f"公式公開データの元ファイルがmanifestと一致しません: {source.original_filename}"
            )

    rows, regions = _read_metrics(metrics_path, manifest)
    return OpenDataSnapshot(
        root=snapshot_root,
        manifest=manifest,
        sources={source.source_id: source for source in manifest.sources},
        rows=rows,
        regions=regions,
    )


def write_open_data_snapshot(
    *,
    rows: list[OpenDataMetricRow] | tuple[OpenDataMetricRow, ...],
    sources: list[OpenDataSource] | tuple[OpenDataSource, ...],
    output_dir: Path,
    force: bool = False,
) -> OpenDataSnapshot:
    """Validate and atomically publish normalized rows and audited source metadata.

    The metrics file is replaced before the manifest. Runtime readers retain their previous
    in-memory snapshot if they happen to observe that very small publication window.
    """

    if not rows:
        raise ValueError("snapshot must contain at least one metric row")
    if not sources:
        raise ValueError("snapshot must contain at least one source")

    target_root = output_dir.expanduser().resolve()
    target_metrics = target_root / "metrics.csv"
    target_manifest = target_root / "manifest.json"
    if not force and (target_metrics.exists() or target_manifest.exists()):
        raise ValueError(
            "output already contains manifest.json or metrics.csv; pass --force to replace them"
        )

    target_root.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    temporary_metrics = target_root / f".metrics.{token}.tmp"
    temporary_manifest = target_root / f".manifest.{token}.tmp"
    try:
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                row.region.municipality_code,
                row.axis.value,
                row.metric_code,
            ),
        )
        with temporary_metrics.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=METRICS_FIELDNAMES)
            writer.writeheader()
            for row in ordered_rows:
                writer.writerow(
                    {
                        "region_code": row.region.municipality_code,
                        "region_name": row.region.name,
                        "prefecture": row.region.prefecture or "",
                        "comparison_group": row.region.comparison_group,
                        "axis": row.axis.value,
                        "metric_code": row.metric_code,
                        "value": row.value,
                        "quality": row.quality,
                        "source_id": row.source_id,
                        "sample_count": row.sample_count if row.sample_count is not None else "",
                        "note": row.note or "",
                    }
                )

        manifest = OpenDataManifest(
            schema_version=1,
            generated_at=datetime.now(UTC),
            metrics_file="metrics.csv",
            metrics_sha256=sha256_file(temporary_metrics),
            sources=list(sources),
        )
        # Validate all foreign keys, metric codes, ranges and region identities before publish.
        _read_metrics(temporary_metrics, manifest)
        temporary_manifest.write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )

        os.replace(temporary_metrics, target_metrics)
        os.replace(temporary_manifest, target_manifest)
    finally:
        temporary_metrics.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)

    return load_open_data_snapshot(target_root)


def build_open_data_snapshot(
    *,
    metrics_path: Path,
    sources_path: Path,
    output_dir: Path,
    force: bool = False,
) -> OpenDataSnapshot:
    """Build a runtime snapshot from manually normalized official downloads."""

    input_metrics = metrics_path.expanduser().resolve()
    input_sources = sources_path.expanduser().resolve()
    if not input_metrics.is_file():
        raise ValueError(f"metrics input does not exist: {input_metrics}")
    if not input_sources.is_file():
        raise ValueError(f"sources input does not exist: {input_sources}")

    try:
        source_payload = json.loads(input_sources.read_text(encoding="utf-8"))
        raw_sources = source_payload["sources"]
        source_inputs = [OpenDataSourceInput.model_validate(item) for item in raw_sources]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"sources input is invalid: {exc}") from exc
    if not source_inputs:
        raise ValueError("sources input must contain at least one source")

    manifest_sources: list[OpenDataSource] = []
    for source in source_inputs:
        original_path = source.original_file.expanduser()
        if not original_path.is_absolute():
            original_path = (input_sources.parent / original_path).resolve()
        if not original_path.is_file():
            raise ValueError(
                f"original official download does not exist for {source.source_id}: {original_path}"
            )
        manifest_sources.append(
            OpenDataSource(
                source_id=source.source_id,
                source_name=source.source_name,
                publisher=source.publisher,
                download_page_url=source.download_page_url,
                license_id=source.license_id,
                license_url=source.license_url,
                attribution=source.attribution,
                retrieved_at=source.retrieved_at,
                reference_date=source.reference_date,
                original_filename=original_path.name,
                original_sha256=sha256_file(original_path),
                commercial_use_note=source.commercial_use_note,
                third_party_rights_note=source.third_party_rights_note,
                transformation_note=source.transformation_note,
            )
        )

    validation_manifest = OpenDataManifest(
        schema_version=1,
        generated_at=datetime.now(UTC),
        metrics_file="metrics.csv",
        metrics_sha256=sha256_file(input_metrics),
        sources=manifest_sources,
    )
    # Validate normalized rows against source IDs before writing any output.
    rows, _ = _read_metrics(input_metrics, validation_manifest)
    return write_open_data_snapshot(
        rows=rows,
        sources=manifest_sources,
        output_dir=output_dir,
        force=force,
    )
