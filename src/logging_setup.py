"""Log to the console (INFO) and to a per-run file under outputs/logs/ (DEBUG)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

_NOISY_LOGGERS = ("google", "google.genai", "google_genai", "urllib3", "anthropic", "httpx", "httpx2", "httpcore", "asyncio")


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    root.addHandler(console)
    root.addHandler(file_handler)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return log_file
