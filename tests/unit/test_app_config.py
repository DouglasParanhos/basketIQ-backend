from __future__ import annotations

from pathlib import Path

import pytest

from basketiq.app_config import DEFAULT_PATH, AppConfigError, load_app_config
from basketiq.config import Settings
from basketiq.pipeline.llm import build_resolver
from basketiq.pipeline.llm.agno_ollama import AgnoOllamaResolver
from basketiq.workers.components import build_components

pytestmark = pytest.mark.req("CFG-01")


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "app-config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = "APP_NAME_FOR_CUSTOMER: Mercado do Zé\nLLM_ENABLED: true\n"


def test_repository_file_holds_the_agreed_values() -> None:
    config = load_app_config(DEFAULT_PATH)
    assert config.app_name_for_customer == "Super Mercado"
    assert config.llm_enabled is False


def test_valid_file(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, VALID))
    assert (config.app_name_for_customer, config.llm_enabled) == ("Mercado do Zé", True)


def test_name_is_trimmed(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, "APP_NAME_FOR_CUSTOMER: '  Loja  '\nLLM_ENABLED: false\n"))
    assert config.app_name_for_customer == "Loja"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("LLM_ENABLED: false\n", "APP_NAME_FOR_CUSTOMER"),  # every key is required: no hidden defaults
        ("APP_NAME_FOR_CUSTOMER: Loja\n", "LLM_ENABLED"),
        ("APP_NAME_FOR_CUSTOMER: Loja\nLLM_ENABLED: false\nOTHER: 1\n", "OTHER"),  # unknown key
        ("APP_NAME_FOR_CUSTOMER: Loja\nLLM_ENABLED: 'false'\n", "LLM_ENABLED"),  # a string is not a boolean
        ("APP_NAME_FOR_CUSTOMER: Loja\nLLM_ENABLED: 0\n", "LLM_ENABLED"),
        ("APP_NAME_FOR_CUSTOMER: ''\nLLM_ENABLED: false\n", "APP_NAME_FOR_CUSTOMER"),
        ("APP_NAME_FOR_CUSTOMER: '   '\nLLM_ENABLED: false\n", "APP_NAME_FOR_CUSTOMER"),
        (f"APP_NAME_FOR_CUSTOMER: {'x' * 41}\nLLM_ENABLED: false\n", "APP_NAME_FOR_CUSTOMER"),
        ("- not\n- a mapping\n", "mapping"),
        ("APP_NAME_FOR_CUSTOMER: [unclosed\n", "Could not read"),
    ],
)
def test_invalid_file_stops_the_process_with_a_clear_message(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(AppConfigError, match=message):
        load_app_config(write(tmp_path, text))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AppConfigError, match="not found"):
        load_app_config(tmp_path / "nope.yaml")


def test_file_is_the_only_source_of_llm_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASKETIQ_LLM_ENABLED", "true")
    with pytest.raises(AppConfigError, match=r"no longer supported.*LLM_ENABLED"):
        load_app_config(write(tmp_path, VALID))


@pytest.mark.req("CFG-03")
def test_llm_flag_controls_whether_the_agent_exists(tmp_path: Path) -> None:
    settings = Settings(env="test")
    off = load_app_config(write(tmp_path, "APP_NAME_FOR_CUSTOMER: A\nLLM_ENABLED: false\n"))
    on = load_app_config(write(tmp_path, "APP_NAME_FOR_CUSTOMER: A\nLLM_ENABLED: true\n"))
    assert build_resolver(off, settings) is None
    assert isinstance(build_resolver(on, settings), AgnoOllamaResolver)


@pytest.mark.req("CFG-03")
def test_worker_components_follow_the_flag(tmp_path: Path) -> None:
    settings = Settings(env="test", ocr_engine="none")
    off = load_app_config(write(tmp_path, "APP_NAME_FOR_CUSTOMER: A\nLLM_ENABLED: false\n"))
    on = load_app_config(write(tmp_path, "APP_NAME_FOR_CUSTOMER: A\nLLM_ENABLED: true\n"))
    assert build_components(settings, off).resolver is None
    assert build_components(settings, on).resolver is not None
