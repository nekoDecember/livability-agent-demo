from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. Mock mode is intentionally the zero-key default."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env.backend",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_mode: Literal["auto", "mock", "openai"] = "auto"
    data_mode: Literal["mock", "government_api"] = "mock"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-luna"
    estat_app_id: SecretStr | None = None
    reinfolib_api_key: SecretStr | None = None

    outputs_dir: Path = PROJECT_ROOT / "outputs"
    devui_host: str = "127.0.0.1"
    devui_port: int = Field(default=8080, ge=1, le=65535)
    devui_auto_open: bool = True
    devui_auth_enabled: bool = False
    mock_latency_ms: int = Field(default=300, ge=0, le=30_000)
    data_timeout_seconds: float = Field(default=60, gt=0, le=600)
    agent_timeout_seconds: float = Field(default=90, gt=0, le=600)
    specialist_attempts: int = Field(default=2, ge=1, le=3)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8091, ge=1, le=65535)
    livability_api_key: SecretStr | None = None

    @property
    def resolved_llm_mode(self) -> Literal["mock", "openai"]:
        if self.llm_mode == "auto":
            return "openai" if self.openai_api_key else "mock"
        return self.llm_mode

    def validate_runtime(self) -> None:
        if self.resolved_llm_mode == "openai" and not self.openai_api_key:
            raise ValueError("LLM_MODE=openai requires OPENAI_API_KEY.")
        if self.data_mode == "government_api":
            missing: list[str] = []
            if not self.estat_app_id:
                missing.append("ESTAT_APP_ID")
            if not self.reinfolib_api_key:
                missing.append("REINFOLIB_API_KEY")
            if missing:
                raise ValueError(
                    "DATA_MODE=government_api requires: " + ", ".join(missing)
                )

    def validate_api_runtime(self) -> None:
        self.validate_runtime()
        try:
            is_loopback = ipaddress.ip_address(self.api_host).is_loopback
        except ValueError:
            is_loopback = self.api_host.lower() == "localhost"
        if not is_loopback and not self.livability_api_key:
            raise ValueError(
                "LIVABILITY_API_KEY is required when API_HOST is not a loopback address."
            )

    def ensure_outputs_dir(self) -> None:
        self.outputs_dir.expanduser().resolve().mkdir(parents=True, exist_ok=True)
