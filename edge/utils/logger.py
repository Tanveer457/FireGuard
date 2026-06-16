# utils/logger.py
# Sets up logging — console + rotating file.

import logging
import logging.handlers
from pathlib import Path


def setup(log_file: str = None, level=logging.INFO):
    fmt     = "%(asctime)s  [%(name)-14s]  %(levelname)-8s  %(message)s"
    datefmt = "%H:%M:%S"

    handlers = [logging.StreamHandler()]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes   = 5 * 1024 * 1024,   # 5 MB per file
                backupCount= 3,
                encoding   = "utf-8",
            )
        )

    logging.basicConfig(
        level    = level,
        format   = fmt,
        datefmt  = datefmt,
        handlers = handlers,
    )

    # Suppress noisy third-party loggers
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)