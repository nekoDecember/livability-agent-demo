import re
from pathlib import Path

from livability_demo.config import PROJECT_ROOT, Settings


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_backend_environment_is_canonical_and_secret_free() -> None:
    assert Settings.model_config["env_file"] == PROJECT_ROOT / ".env.backend"

    values = _env_values(PROJECT_ROOT / ".env.backend.example")
    assert values["LLM_MODE"] == "mock"
    assert values["OPENAI_API_KEY"] == ""
    assert values["OPENAI_MODEL"] == "gpt-5.6-luna"
    assert values["LIVABILITY_API_BIND_ADDRESS"] == "127.0.0.1"
    assert values["LIVABILITY_API_PORT"] == "8091"
    assert values["LIVABILITY_FRONTEND_BIND_ADDRESS"] == "0.0.0.0"
    assert values["LIVABILITY_FRONTEND_PORT"] == "5173"
    assert values["LIVABILITY_INTERNAL_NETWORK_NAME"] == "livability-agent-internal"
    assert values["OPENWEBUI_ADMIN_ENV_FILE"] == ""


def test_local_backend_environment_contains_only_documented_keys_when_present() -> None:
    actual = PROJECT_ROOT / ".env.backend"
    if not actual.exists():
        return

    expected_keys = set(_env_values(PROJECT_ROOT / ".env.backend.example"))
    actual_keys = set(_env_values(actual))
    # Newly documented settings all have safe defaults, so an older local file remains valid.
    assert actual_keys <= expected_keys


def test_openwebui_override_uses_canonical_project_environment() -> None:
    override = (PROJECT_ROOT / "compose.openwebui.override.yaml").read_text(encoding="utf-8")

    assert "path: .env.backend" in override
    assert "${OPENWEBUI_ADMIN_ENV_FILE:?" in override
    assert "../../platforms/openwebui-platform" not in override
    assert ".env.openwebui" not in override
    assert not (PROJECT_ROOT / ".env.openwebui.example").exists()


def test_example_covers_all_compose_variables() -> None:
    compose = "\n".join(
        (PROJECT_ROOT / name).read_text(encoding="utf-8")
        for name in (
            "compose.yaml",
            "compose.public.yaml",
            "compose.openwebui.override.yaml",
        )
    )
    compose_keys = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose))
    # PUBLIC_URL is injected by the public-gateway manifest, not by the
    # Livability backend environment file.
    compose_keys.discard("PUBLIC_URL")

    gateway_only = {"PUBLIC_URL"}
    assert compose_keys - gateway_only <= set(_env_values(PROJECT_ROOT / ".env.backend.example"))


def test_public_compose_exposes_the_dedicated_frontend_only() -> None:
    public = (PROJECT_ROOT / "compose.public.yaml").read_text(encoding="utf-8")

    assert "  livability-agent-frontend:" in public
    assert "ports: !reset []" in public
    assert "PUBLIC_URL: ${PUBLIC_URL:?Set PUBLIC_URL}" in public
    assert "openwebui-agent-network" not in public


def test_docker_build_uses_locked_dependencies() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml uv.lock README.md" in dockerfile
    assert "uv sync --locked --no-dev" in dockerfile
    assert "pip install" not in dockerfile
