"""Per-cycle file logger for the supervisor service."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_LOG_DIR = Path(os.getenv("SUPERVISOR_LOG_DIR", "logs/supervisor"))


def open_cycle_logger(now_utc: datetime | None = None) -> logging.Logger:
    """Return a logger that writes to a per-cycle file under logs/supervisor/.

    The file is named YYYY-MM-DD_HH-MM.log using UTC time.
    Also streams to stdout for Docker log aggregation.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    filename = now_utc.strftime("%Y-%m-%d_%H-%M") + ".log"
    log_path = _LOG_DIR / filename

    logger = logging.getLogger(f"supervisor.cycle.{now_utc.strftime('%Y%m%d_%H%M')}")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.info(f"Supervisor cycle started — log: {log_path}")
    return logger
