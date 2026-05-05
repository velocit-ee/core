"""Centralised log configuration for all velocitee engines.

We use **structlog** as the formatter and **stdlib `logging`** as the
transport. Engines (and renderers, adapters, scripts) keep using
`logging.getLogger(__name__)` exactly as before — no migration burden,
no churn — but the output is structured and consistent across the stack.

Why this shape:

  - VLE will eventually ingest these logs for documentation and drift.
    Structured key/value output is grep-able and schema-able; raw printf
    isn't.
  - Operators tail these on a console most of the time. The default
    renderer is human-friendly (key=value, colour on TTY); switching to
    JSON for log shipping is a single env-var toggle.
  - We don't want to maintain log call-sites in two styles. Configuring
    structlog as a stdlib *processor* keeps the call-site style identical
    while changing how the records are rendered.

Call `configure()` exactly once, very early in each engine's main(). Idempotent
on repeat calls — you can re-call it safely under tests.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Literal

import structlog

LogFormat = Literal["console", "json"]


def configure(
    *,
    level: int | str = logging.INFO,
    fmt: LogFormat | None = None,
    stream=None,
) -> None:
    """Wire structlog into stdlib logging. Safe to call multiple times.

    `fmt` controls the renderer:
      - 'console' (default on TTY) — colour key=value, friendly for humans
      - 'json' (default off TTY, or VELOCITEE_LOG_FORMAT=json) — newline-
        delimited JSON, friendly for log shippers / VLE ingestion

    `level` accepts an int (logging.INFO) or a string ('INFO', 'DEBUG').
    The VELOCITEE_LOG_LEVEL env var overrides whatever is passed.
    """
    stream = stream or sys.stderr
    env_level = os.environ.get("VELOCITEE_LOG_LEVEL")
    if env_level:
        level = env_level
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
        if not isinstance(level, int):
            level = logging.INFO

    if fmt is None:
        env_fmt = os.environ.get("VELOCITEE_LOG_FORMAT", "").lower()
        if env_fmt in ("console", "json"):
            fmt = env_fmt  # type: ignore[assignment]
        else:
            fmt = "console" if _isatty(stream) else "json"

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Pre-chain — runs on records that originate from stdlib logging too,
    # so a `logging.getLogger("foo").info("bar", extra={"k": "v"})` call
    # is rendered the same way as a structlog.get_logger() call.
    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        timestamper,
    ]

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer(sort_keys=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=_isatty(stream))

    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    # Replace existing stream handlers so re-configure() doesn't double up.
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    # structlog-native side: any code that does `structlog.get_logger()`
    # gets the same processor chain. Both paths converge on the renderer
    # via ProcessorFormatter.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    """Convenience: return a structlog logger. Equivalent to logging.getLogger
    but with the .bind(...) chainable API for adding context fields."""
    return structlog.get_logger(name) if name else structlog.get_logger()


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False
