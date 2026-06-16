"""
utils/logger.py — Rotating log file setup

Logs go to TWO places simultaneously:
  1. Console  (stdout) — coloured, easy to read while developing
  2. File     (logs/server.log) — rotates at 5MB, keeps 5 backups

If the server crashes, you still have full logs in logs/server.log.
Without file logging, all crash information is lost forever.

Usage — call setup_logging() once at startup in main.py:
    from utils.logger import setup_logging
    setup_logging()
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from server.utils.paths import LOGS_DIR


def setup_logging(log_level: str = "INFO", log_dir: str = str(LOGS_DIR)):
    """
    Configure root logger with console + rotating file handlers.

    Args:
        log_level: "DEBUG", "INFO", "WARNING", "ERROR"
        log_dir:   directory for log files (created if missing)
    """
    # Create logs directory
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / "server.log"

    level = getattr(logging, log_level.upper(), logging.INFO)

    # ── Format ────────────────────────────────────────────────────────────────
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    # ── Console handler ───────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)

    # ── Rotating file handler ─────────────────────────────────────────────────
    # maxBytes=5MB, backupCount=5 → keeps last 25MB of logs total
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # ── Root logger ───────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        f"Logging initialised — level={log_level} file={log_file}"
    )