from __future__ import annotations

import logging

from agent_framework.devui import register_cleanup, serve

from .config import Settings
from .facade import LivabilityCoordinatorAgent


def build_agent(settings: Settings | None = None) -> LivabilityCoordinatorAgent:
    return LivabilityCoordinatorAgent(settings=settings or Settings())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    agent = build_agent(settings)
    register_cleanup(agent, agent.close)
    logging.getLogger(__name__).info(
        "Starting Livability Demo: llm_mode=%s data_mode=%s model=%s",
        settings.resolved_llm_mode,
        settings.data_mode,
        settings.openai_model,
    )
    serve(
        entities=[agent],
        host=settings.devui_host,
        port=settings.devui_port,
        auto_open=settings.devui_auto_open,
        auth_enabled=settings.devui_auth_enabled,
        mode="developer",
    )


if __name__ == "__main__":
    main()
