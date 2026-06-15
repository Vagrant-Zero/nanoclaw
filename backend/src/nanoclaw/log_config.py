"""Centralized logging configuration — all logs to ``.nanoclaw/logs/``.

Usage::

    from nanoclaw.log_config import setup_logging
    setup_logging()  # Call once at startup

Then use standard ``logging.getLogger(__name__)`` in any module.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from nanoclaw.config import settings


def _get_log_dir() -> Path:
    """Return the log directory path, creating it if needed."""
    log_dir = Path(settings.home) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(
    log_level: int = logging.DEBUG,
    console_level: int = logging.WARNING,
    log_name: str = "nanoclaw.log",
) -> None:
    """Configure logging to write to both file and console.

    File handler: ``{home}/logs/{log_name}`` at *log_level* (default DEBUG).
    Console handler: stderr at *console_level* (default WARNING).

    Safe to call multiple times — subsequent calls are no-ops if the
    file handler is already configured.
    """
    logger = logging.getLogger("nanoclaw")
    if logger.handlers:
        return  # Already configured

    logger.setLevel(log_level)

    log_dir = _get_log_dir()
    file_path = log_dir / log_name

    file_handler = logging.FileHandler(
        str(file_path), mode="a", encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)-40s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_fmt = logging.Formatter(
        "[%(levelname)-8s] %(name)-30s %(message)s",
    )
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # Redirect root logger so all 'import logging; logging.getLogger()' logs
    # end up in the same file
    root = logging.getLogger()
    root.setLevel(log_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    logger.info("Logging configured — writing to %s", file_path)
