"""Logging setup with credential redaction.

The filter scrubs two things: literal secret values registered at configure time,
and anything that looks like an HTTP authorization header, which is how a token
most often escapes into a log by accident (via an httpx request dump).
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, Sequence

REDACTED = "***REDACTED***"

#: Used to render ``exc_info`` inside the filter. ``logging.Formatter`` only populates
#: ``record.exc_text`` when it formats the record, which happens *after* filters run —
#: so a secret inside a traceback would otherwise bypass redaction entirely.
_EXC_FORMATTER = logging.Formatter()

_HEADER_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"(?i)\b(authorization)\s*[:=]\s*['\"]?(basic|bearer)\s+[\w\-._~+/=]+['\"]?"),
    re.compile(r"(?i)\b(basic|bearer)\s+[A-Za-z0-9\-._~+/]{16,}={0,2}"),
    re.compile(r"(?i)(api[_-]?token|jira[_-]?api[_-]?token)\s*[:=]\s*['\"]?[^\s'\"&]+['\"]?"),
)


class RedactingFilter(logging.Filter):
    """Replace known secrets and authorization headers in every log record."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Longest first, so a token that contains a shorter secret is scrubbed whole.
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _HEADER_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub_value(a) for a in record.args)
        # Render the traceback here so it can be scrubbed. Setting exc_text makes
        # the formatter reuse it rather than re-rendering the unscrubbed original.
        if record.exc_info and not record.exc_text:
            record.exc_text = _EXC_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self.scrub(record.stack_info)
        return True

    def _scrub_value(self, value: object) -> object:
        return self.scrub(value) if isinstance(value, str) else value


def configure_logging(
    level: str = "INFO",
    secrets: Iterable[str] = (),
    *,
    stream: object | None = None,
) -> logging.Logger:
    """Configure the ``jiralens`` logger. Safe to call more than once."""
    logger = logging.getLogger("jiralens")
    logger.setLevel(level.upper())
    logger.propagate = False

    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stderr)  # type: ignore[arg-type]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    )
    handler.addFilter(RedactingFilter(secrets))
    logger.addHandler(handler)

    # httpx logs full request URLs at INFO; keep it quiet unless we are debugging.
    logging.getLogger("httpx").setLevel("WARNING")
    return logger


def get_logger(name: str = "jiralens") -> logging.Logger:
    return logging.getLogger(name)


__all__ = ["REDACTED", "RedactingFilter", "configure_logging", "get_logger"]
