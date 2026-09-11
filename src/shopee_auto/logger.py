"""Console + rotating file logging shared by every module."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from shopee_auto.config import LoggingConfig

_CONFIGURED = False


def setup_logging(cfg: LoggingConfig) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, cfg.level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    root = logging.getLogger("shopee_auto")
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        cfg.log_dir / "pipeline.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"shopee_auto.{name}")
