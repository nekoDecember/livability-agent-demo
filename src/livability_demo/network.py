from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.backend"
_PROXY_VARIABLES = (
    ("http_proxy", "HTTP_PROXY"),
    ("https_proxy", "HTTPS_PROXY"),
    ("all_proxy", "ALL_PROXY"),
)
_DEFAULT_NO_PROXY = (
    "localhost",
    "127.0.0.1",
    "::1",
    "livability-agent-api",
    "openwebui",
)


def _first_non_empty(
    values: Mapping[str, str | None], names: tuple[str, str]
) -> str | None:
    for name in names:
        value = values.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _no_proxy_entries(values: Mapping[str, str | None]) -> list[str]:
    entries: list[str] = []
    for name in ("no_proxy", "NO_PROXY"):
        value = values.get(name)
        if value:
            entries.extend(item.strip() for item in value.split(",") if item.strip())
    return entries


def resolve_proxy_environment(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve HTTP proxy variables without loading unrelated dotenv values.

    Non-empty process variables take precedence over `.env.backend`. When both
    casings are set in one source, the lowercase spelling takes precedence.
    NO_PROXY entries are additive so local and Compose service addresses remain
    reachable even when a custom bypass list is configured.
    """

    process_values = os.environ if environ is None else environ
    dotenv = dotenv_values(env_file or DEFAULT_ENV_FILE, encoding="utf-8")
    resolved: dict[str, str] = {}

    proxy_values: dict[str, str] = {}
    for lowercase, uppercase in _PROXY_VARIABLES:
        value = _first_non_empty(process_values, (lowercase, uppercase))
        if value is None:
            value = _first_non_empty(dotenv, (lowercase, uppercase))
        if value is not None:
            proxy_values[lowercase] = value

    # HTTP proxy variables are commonly configured as a single shared proxy.
    # HTTPX otherwise treats HTTP_PROXY and HTTPS_PROXY as separate routes.
    if "https_proxy" not in proxy_values and "http_proxy" in proxy_values:
        proxy_values["https_proxy"] = proxy_values["http_proxy"]

    for lowercase, uppercase in _PROXY_VARIABLES:
        value = proxy_values.get(lowercase)
        if value is not None:
            resolved[lowercase] = value
            resolved[uppercase] = value

    bypass_entries = [
        *_DEFAULT_NO_PROXY,
        *_no_proxy_entries(dotenv),
        *_no_proxy_entries(process_values),
    ]
    unique_entries: list[str] = []
    seen: set[str] = set()
    for entry in bypass_entries:
        normalized = entry.lower()
        if normalized not in seen:
            seen.add(normalized)
            unique_entries.append(entry)
    no_proxy = ",".join(unique_entries)
    resolved["no_proxy"] = no_proxy
    resolved["NO_PROXY"] = no_proxy
    return resolved


def configure_proxy_environment(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Expose only resolved proxy settings to libraries that read process env."""

    resolved = resolve_proxy_environment(env_file=env_file, environ=environ)
    if environ is None:
        for lowercase, uppercase in _PROXY_VARIABLES:
            value = resolved.get(lowercase)
            if value is not None:
                os.environ[lowercase] = value
                os.environ[uppercase] = value
        os.environ["no_proxy"] = resolved["no_proxy"]
        os.environ["NO_PROXY"] = resolved["NO_PROXY"]
    return resolved


def create_async_http_client(**client_options: Any) -> httpx.AsyncClient:
    """Create a normal HTTPX client using proxy settings from env and dotenv."""

    configure_proxy_environment()
    client_options.setdefault("trust_env", True)
    return httpx.AsyncClient(**client_options)
