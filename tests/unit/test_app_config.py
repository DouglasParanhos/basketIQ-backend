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


DEFAULTS: dict[str, object] = {
    "APP_NAME_FOR_CUSTOMER": "Loja",
    "LLM_ENABLED": False,
    "SMART_LIST_ENABLED": False,
    "GOAL_RING_GREEN_UP_TO_PCT": 50,
    "GOAL_RING_ORANGE_UP_TO_PCT": 75,
    "AVERAGE_MIN_MONTHS": 2,
    "AVERAGE_ON_TARGET_PCT": 5,
}


def cfg(**changes: object) -> str:
    """The file with every key at its default, some changed; a value of None drops the key."""
    values = {**DEFAULTS, **changes}
    return "".join(f"{k}: {v}\n" for k, v in values.items() if v is not None)


VALID = cfg(APP_NAME_FOR_CUSTOMER="Mercado do Zé", LLM_ENABLED="true", SMART_LIST_ENABLED="true")


def test_repository_file_holds_the_agreed_values() -> None:
    config = load_app_config(DEFAULT_PATH)
    assert config.app_name_for_customer == "Super Mercado"
    assert config.llm_enabled is False
    assert config.smart_list_enabled is False  # the smart list stays off until it is approved


@pytest.mark.req("F-176")
def test_repository_file_holds_the_ring_and_average_values() -> None:
    config = load_app_config(DEFAULT_PATH)
    assert (config.goal_ring_green_up_to_pct, config.goal_ring_orange_up_to_pct) == (50, 75)
    assert (config.average_min_months, config.average_on_target_pct) == (2, 5)


def test_valid_file(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, VALID))
    assert (config.app_name_for_customer, config.llm_enabled, config.smart_list_enabled) == (
        "Mercado do Zé",
        True,
        True,
    )


def test_name_is_trimmed(tmp_path: Path) -> None:
    config = load_app_config(write(tmp_path, cfg(APP_NAME_FOR_CUSTOMER="'  Loja  '")))
    assert config.app_name_for_customer == "Loja"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (cfg(APP_NAME_FOR_CUSTOMER=None), "APP_NAME_FOR_CUSTOMER"),  # every key is required
        (cfg(LLM_ENABLED=None), "LLM_ENABLED"),  # no hidden defaults
        (cfg(SMART_LIST_ENABLED=None), "SMART_LIST_ENABLED"),  # F-112: the new key too
        (cfg() + "OTHER: 1\n", "OTHER"),
        (cfg(LLM_ENABLED="'false'"), "LLM_ENABLED"),
        (cfg(LLM_ENABLED=0), "LLM_ENABLED"),
        (cfg(SMART_LIST_ENABLED="'true'"), "SMART_LIST_ENABLED"),
        (cfg(SMART_LIST_ENABLED=1), "SMART_LIST_ENABLED"),
        (cfg(APP_NAME_FOR_CUSTOMER="''"), "APP_NAME_FOR_CUSTOMER"),
        (cfg(APP_NAME_FOR_CUSTOMER="'   '"), "APP_NAME_FOR_CUSTOMER"),
        (cfg(APP_NAME_FOR_CUSTOMER="x" * 41), "APP_NAME_FOR_CUSTOMER"),
        ("- not\n- a mapping\n", "mapping"),
        ("APP_NAME_FOR_CUSTOMER: [unclosed\n", "Could not read"),
    ],
)
@pytest.mark.req("F-112")
def test_invalid_file_stops_the_process_with_a_clear_message(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(AppConfigError, match=message):
        load_app_config(write(tmp_path, text))


NUMBER_KEYS = [
    "GOAL_RING_GREEN_UP_TO_PCT",
    "GOAL_RING_ORANGE_UP_TO_PCT",
    "AVERAGE_MIN_MONTHS",
    "AVERAGE_ON_TARGET_PCT",
]


@pytest.mark.req("F-176")
@pytest.mark.parametrize("key", NUMBER_KEYS)
@pytest.mark.parametrize("bad", ["'50'", "50.5", "true", "null"])
def test_the_ring_and_average_keys_are_strict_integers(tmp_path: Path, key: str, bad: str) -> None:
    with pytest.raises(AppConfigError, match=key):
        load_app_config(write(tmp_path, cfg(**{key: bad})))


@pytest.mark.req("F-176")
@pytest.mark.parametrize("key", NUMBER_KEYS)
def test_the_ring_and_average_keys_are_required(tmp_path: Path, key: str) -> None:
    with pytest.raises(AppConfigError, match=key):
        load_app_config(write(tmp_path, cfg(**{key: None})))


@pytest.mark.req("F-176")
@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"GOAL_RING_GREEN_UP_TO_PCT": 0}, "GOAL_RING_GREEN_UP_TO_PCT"),
        ({"GOAL_RING_ORANGE_UP_TO_PCT": 101}, "GOAL_RING_ORANGE_UP_TO_PCT"),
        ({"AVERAGE_MIN_MONTHS": 0}, "AVERAGE_MIN_MONTHS"),
        ({"AVERAGE_MIN_MONTHS": 13}, "AVERAGE_MIN_MONTHS"),
        ({"AVERAGE_ON_TARGET_PCT": -1}, "AVERAGE_ON_TARGET_PCT"),
        ({"AVERAGE_ON_TARGET_PCT": 51}, "AVERAGE_ON_TARGET_PCT"),
        ({"GOAL_RING_GREEN_UP_TO_PCT": 75}, "GOAL_RING_GREEN_UP_TO_PCT"),  # green equal to orange
        ({"GOAL_RING_GREEN_UP_TO_PCT": 80}, "GOAL_RING_GREEN_UP_TO_PCT"),  # green above orange
    ],
)
def test_the_ring_and_average_keys_are_within_their_limits(
    tmp_path: Path, changes: dict[str, object], key: str
) -> None:
    with pytest.raises(AppConfigError, match=key):
        load_app_config(write(tmp_path, cfg(**changes)))


@pytest.mark.req("F-176")
def test_the_limits_are_accepted_at_their_edges(tmp_path: Path) -> None:
    config = load_app_config(
        write(
            tmp_path,
            cfg(
                GOAL_RING_GREEN_UP_TO_PCT=1,
                GOAL_RING_ORANGE_UP_TO_PCT=100,
                AVERAGE_MIN_MONTHS=12,
                AVERAGE_ON_TARGET_PCT=0,
            ),
        )
    )
    assert (config.goal_ring_green_up_to_pct, config.goal_ring_orange_up_to_pct) == (1, 100)
    assert (config.average_min_months, config.average_on_target_pct) == (12, 0)


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
    off = load_app_config(write(tmp_path, cfg(LLM_ENABLED="false")))
    on = load_app_config(write(tmp_path, cfg(LLM_ENABLED="true")))
    assert build_resolver(off, settings) is None
    assert isinstance(build_resolver(on, settings), AgnoOllamaResolver)


@pytest.mark.req("CFG-03")
def test_worker_components_follow_the_flag(tmp_path: Path) -> None:
    settings = Settings(env="test", ocr_engine="none")
    off = load_app_config(write(tmp_path, cfg(LLM_ENABLED="false")))
    on = load_app_config(write(tmp_path, cfg(LLM_ENABLED="true")))
    assert build_components(settings, off).resolver is None
    assert build_components(settings, on).resolver is not None
