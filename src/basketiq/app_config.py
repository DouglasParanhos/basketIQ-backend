"""Product configuration file (config/app-config.yaml): customer-facing name and feature flags.

Every key is required and there are no defaults in code, so the file is the complete list of
these decisions. Loading is strict: unknown keys, wrong types or an empty name stop the process
at start-up instead of silently falling back.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    model_validator,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "app-config.yaml"
PATH_ENV_VAR = "BASKETIQ_APP_CONFIG_FILE"

# Values that used to be environment variables and now live only in the file.
MOVED_ENV_VARS = {"BASKETIQ_LLM_ENABLED": "LLM_ENABLED"}


class AppConfigError(RuntimeError):
    """The configuration file is missing or invalid."""


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=False)

    app_name_for_customer: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ] = Field(alias="APP_NAME_FOR_CUSTOMER")
    llm_enabled: StrictBool = Field(alias="LLM_ENABLED")
    smart_list_enabled: StrictBool = Field(alias="SMART_LIST_ENABLED")
    # Home ring (RF-15): green up to this percentage of the goal, orange up to the next one, red above.
    goal_ring_green_up_to_pct: StrictInt = Field(alias="GOAL_RING_GREEN_UP_TO_PCT", ge=1)
    goal_ring_orange_up_to_pct: StrictInt = Field(alias="GOAL_RING_ORANGE_UP_TO_PCT", le=100)
    average_min_months: StrictInt = Field(alias="AVERAGE_MIN_MONTHS", ge=1, le=12)
    average_on_target_pct: StrictInt = Field(alias="AVERAGE_ON_TARGET_PCT", ge=0, le=50)

    @model_validator(mode="after")
    def _green_below_orange(self) -> AppConfig:
        if self.goal_ring_green_up_to_pct >= self.goal_ring_orange_up_to_pct:
            raise ValueError("GOAL_RING_GREEN_UP_TO_PCT: must be lower than GOAL_RING_ORANGE_UP_TO_PCT")
        return self


def config_path() -> Path:
    override = os.environ.get(PATH_ENV_VAR)
    return Path(override) if override else DEFAULT_PATH


def load_app_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    for env_name, key in MOVED_ENV_VARS.items():
        if env_name in os.environ:
            raise AppConfigError(
                f"{env_name} is no longer supported: set {key} in {path.name} instead "
                "(the file is the only source of this value)."
            )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppConfigError(f"Configuration file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise AppConfigError(f"Could not read configuration file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AppConfigError(f"{path.name} must be a mapping of KEY: value pairs.")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise AppConfigError(f"Invalid configuration in {path.name}: {problems}") from exc


@lru_cache
def get_app_config() -> AppConfig:
    return load_app_config()
