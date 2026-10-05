from __future__ import annotations

import argparse
from pathlib import Path

from .open_data import build_open_data_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a validated Livability snapshot from manually downloaded official files "
            "and a normalized metrics CSV. This command performs no network access."
        )
    )
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("open_data"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    snapshot = build_open_data_snapshot(
        metrics_path=args.metrics,
        sources_path=args.sources,
        output_dir=args.output,
        force=args.force,
    )
    print(
        f"Validated snapshot: {snapshot.root} / "
        f"{len(snapshot.regions)} regions / {len(snapshot.rows)} metrics / "
        f"{len(snapshot.sources)} sources"
    )
