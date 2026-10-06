from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .official_data_sync import (
    DASHBOARD_SOURCE_ID,
    LAND_SURVEY_SOURCE_ID,
    NPA_TRAFFIC_SOURCE_ID,
    TERTIARY_SCHOOL_SOURCE_ID,
    sync_official_open_data,
)
from .open_data import load_open_data_snapshot


def check_output_writable(output: Path) -> None:
    try:
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(prefix=".livability-write-check-", dir=output):
            pass
    except OSError as exc:
        if exc.errno not in (13, 30):  # permission denied / read-only filesystem
            raise
        owner = output.stat() if output.exists() else None
        owner_text = f"{owner.st_uid}:{owner.st_gid}" if owner else "unknown"
        raise PermissionError(
            f"Official data directory is not writable: {output}; "
            f"sync UID:GID={os.geteuid()}:{os.getegid()}, directory owner={owner_text}. "
            "Check the host directory permissions and read-only mount settings. "
            "Set LIVABILITY_HOST_UID and LIVABILITY_HOST_GID together if needed; "
            "empty values select the mount owner in Compose. Existing data was not changed."
        ) from exc


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    if args.if_enabled and settings.data_mode != "open_data":
        print(f"Official data sync skipped because DATA_MODE={settings.data_mode}.")
        return 0

    output: Path = args.output or settings.open_data_dir
    check_output_writable(output)
    all_regions = args.all_regions or (
        not args.region and settings.open_data_sync_scope == "nationwide"
    )
    regions = tuple(args.region) if args.region else settings.open_data_region_names
    include_traffic = settings.open_data_include_traffic and not args.without_traffic
    if not args.force_refresh:
        try:
            current = load_open_data_snapshot(output)
        except Exception:
            pass
        else:
            generated_at = current.manifest.generated_at
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - generated_at).total_seconds() / 3_600
            present_names = {region.name for region in current.regions.values()}
            dashboard_sources = [
                source
                for source_id, source in current.sources.items()
                if source_id == DASHBOARD_SOURCE_ID
                or source_id.startswith(f"{DASHBOARD_SOURCE_ID}-")
            ]
            fresh_dashboard_sources = [
                source
                for source in dashboard_sources
                if (datetime.now(UTC).date() - source.retrieved_at).days * 24
                <= settings.open_data_sync_max_age_hours
            ]
            dashboard_source = next(
                (
                    source
                    for source in fresh_dashboard_sources
                    if "取得範囲=全国市区町村" in source.transformation_note
                ),
                None,
            )
            traffic_is_ready = not include_traffic or any(
                source_id == NPA_TRAFFIC_SOURCE_ID
                or source_id.startswith(f"{NPA_TRAFFIC_SOURCE_ID}-")
                for source_id in current.sources
            )
            land_survey_sources = [
                source
                for source_id, source in current.sources.items()
                if source_id == LAND_SURVEY_SOURCE_ID
                or source_id.startswith(f"{LAND_SURVEY_SOURCE_ID}-")
            ]
            land_survey_is_fresh = any(
                (datetime.now(UTC).date() - source.retrieved_at).days * 24
                <= settings.open_data_sync_max_age_hours
                for source in land_survey_sources
            )
            tertiary_school_is_fresh = any(
                (datetime.now(UTC).date() - source.retrieved_at).days * 24
                <= settings.open_data_sync_max_age_hours
                for source_id, source in current.sources.items()
                if source_id == TERTIARY_SCHOOL_SOURCE_ID
                or source_id.startswith(f"{TERTIARY_SCHOOL_SOURCE_ID}-")
            )
            scope_is_ready = (
                dashboard_source is not None
                if all_regions
                else bool(fresh_dashboard_sources) and set(regions).issubset(present_names)
            )
            if (
                age_hours <= settings.open_data_sync_max_age_hours
                and scope_is_ready
                and traffic_is_ready
                and land_survey_is_fresh
                and tertiary_school_is_fresh
            ):
                print(
                    f"Official data snapshot is fresh ({age_hours:.1f}h old); "
                    "network refresh skipped."
                )
                return 0
    try:
        result = await sync_official_open_data(
            region_names=regions,
            output_dir=output,
            traffic_csv_url=settings.open_data_traffic_csv_url,
            traffic_reference_year=settings.open_data_traffic_reference_year,
            traffic_expected_sha256=settings.open_data_traffic_expected_sha256,
            include_traffic=include_traffic,
            all_regions=all_regions,
            timeout_seconds=settings.open_data_sync_timeout_seconds,
            minimum_interval_seconds=settings.open_data_sync_request_interval_seconds,
        )
    except Exception as exc:
        if args.allow_stale:
            try:
                stale = load_open_data_snapshot(output)
            except Exception:
                pass
            else:
                print(
                    "Official data refresh failed; continuing with the last validated "
                    f"snapshot ({stale.manifest.generated_at.isoformat()}): {exc}",
                    file=sys.stderr,
                )
                return 0
        raise

    names = (
        f"全国{len(result.refreshed_regions)}地域"
        if all_regions
        else "、".join(region.name for region in result.refreshed_regions)
    )
    print(
        f"Official data snapshot refreshed: {result.snapshot.root} / {names} / "
        f"{result.refreshed_metric_count} refreshed metrics / "
        f"{len(result.snapshot.rows)} total metrics"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch documented, no-registration government data and publish a validated "
            "Livability snapshot. Existing non-conflicting snapshot rows are preserved."
        )
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--region",
        action="append",
        help="Municipality name. Repeat for selected-only sync.",
    )
    scope.add_argument(
        "--all-regions",
        action="store_true",
        help="Build a nationwide municipality snapshot (the default configured scope).",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--without-traffic",
        action="store_true",
        help="Skip the official NPA traffic-accident CSV download.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Return success on refresh failure only when a valid previous snapshot exists.",
    )
    parser.add_argument(
        "--if-enabled",
        action="store_true",
        help="Skip without network access unless DATA_MODE=open_data.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Refresh even when the current snapshot is younger than the configured max age.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args, Settings())))
