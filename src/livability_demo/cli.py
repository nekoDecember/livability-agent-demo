from __future__ import annotations

import argparse
import asyncio

from .config import Settings
from .orchestrator import LivabilityOrchestrator


async def _run(request: str) -> None:
    settings = Settings()
    orchestrator = LivabilityOrchestrator(settings)
    try:
        report, markdown = await orchestrator.assess(request)
        print(markdown)
        print(f"\nMarkdown: {report.artifacts.markdown_path}")
        print(f"JSON: {report.artifacts.json_path}")
    finally:
        await orchestrator.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a livability assessment without DevUI.")
    parser.add_argument(
        "request",
        nargs="?",
        default="流山市の住みやすさを5軸均等で評価して",
        help="Natural-language assessment request",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.request))


if __name__ == "__main__":
    main()

