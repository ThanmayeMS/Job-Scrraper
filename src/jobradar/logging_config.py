"""Minimal structured logging setup. Call configure_logging() once at startup."""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Quiet noisy third-party loggers.
    for noisy in ("httpx", "urllib3", "selenium", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
