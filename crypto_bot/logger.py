"""
logger.py — structured, consistent logging for the whole bot.

One configured root-ish logger ("crypto_bot"); every module calls
get_logger(__name__) and gets a child with a uniform UTC-timestamped format.
Idempotent: safe to call repeatedly (won't double-attach handlers).
"""

from __future__ import annotations

import logging
import sys
import time

_CONFIGURED = False
_FORMAT = "%(asctime)sZ | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def configure(level: str = "INFO") -> None:
    """Configure the 'crypto_bot' logger once. Timestamps are UTC."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    logger = logging.getLogger("crypto_bot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    formatter.converter = time.gmtime  # force UTC, never local time
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str = "crypto_bot") -> logging.Logger:
    """Return a child logger under the 'crypto_bot' namespace."""
    if not _CONFIGURED:
        configure()
    if name == "crypto_bot" or name.startswith("crypto_bot."):
        return logging.getLogger(name)
    # Normalize e.g. "crypto_bot.state" from a module __name__.
    short = name.split(".")[-1]
    return logging.getLogger(f"crypto_bot.{short}")
