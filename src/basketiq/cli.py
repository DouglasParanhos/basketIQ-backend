"""Operational commands: `python -m basketiq.cli <command>`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from basketiq.config import get_settings
from basketiq.observability.logging import configure_logging
from basketiq.storage import get_storage

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def migrate() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


def init_storage() -> None:
    settings = get_settings()
    get_storage().ensure_bucket([settings.frontend_base_url])


def main() -> None:
    configure_logging(get_settings().log_level)
    parser = argparse.ArgumentParser(prog="basketiq")
    parser.add_argument("command", choices=["migrate", "init-storage", "migrate-and-init"])
    args = parser.parse_args()
    if args.command in ("migrate", "migrate-and-init"):
        migrate()
        log.info("migrations_applied")
    if args.command in ("init-storage", "migrate-and-init"):
        init_storage()
        log.info("storage_ready")


if __name__ == "__main__":
    main()
