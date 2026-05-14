"""structlog setup.

``configure_logging()`` wires structlog into the stdlib logger so existing
``print(file=sys.stderr)`` calls keep working but new code can do
``log.info("event", key=value)`` and get structured JSON.

When ``structlog`` is missing, the function falls back to ``logging.basicConfig``
so the binary keeps starting.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

try:
    import structlog  # type: ignore
    _HAVE_STRUCTLOG = True
except ImportError:
    _HAVE_STRUCTLOG = False


def configure_logging(
    *,
    json: bool | None = None,
    level: str = "INFO",
    log_dir: str | None = None,
) -> None:
    """Initialise both stdlib and structlog formatters.

    ``json`` defaults to True when running under systemd
    (``$INVOCATION_ID`` is set), False otherwise. ``log_dir`` may point
    at a directory; files are written as ``tracking-engine.log`` and
    rotated externally by logrotate.
    """
    if json is None:
        json = bool(os.environ.get("INVOCATION_ID"))

    handlers: list[logging.Handler] = []
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    if log_dir:
        log_path = Path(log_dir) / "tracking-engine.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        handlers.append(fh)
    else:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter(fmt))
        handlers.append(sh)

    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not _HAVE_STRUCTLOG:
        return

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "tracking"):
    if _HAVE_STRUCTLOG:
        import structlog

        return structlog.get_logger(name)
    return logging.getLogger(name)
