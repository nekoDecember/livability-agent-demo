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


def test_local_backend_environment_matches_example_when_present() -> None:
    actual = PROJECT_ROOT / ".env.backend"
    if not actual.exists():
        return

    expected_keys = set(_env_values(PROJECT_ROOT / ".env.backend.example"))
    actual_keys = set(_env_values(actual))
    assert actual_keys == expected_keys


def test_openwebui_override_uses_canonical_project_environment() -> None:
    override = (PROJECT_ROOT / "compose.openwebui.override.yaml").read_text(encoding="utf-8")

    assert "path: .env.backend" in override
    assert ".env.openwebui" not in override
    assert not (PROJECT_ROOT / ".env.openwebui.example").exists()
