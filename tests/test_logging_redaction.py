"""The token must never reach a log line, however it gets there."""

from __future__ import annotations

import io
import logging

import pytest

from jiralens.logging_config import REDACTED, RedactingFilter, configure_logging

TOKEN = "ATATT3xFfGF0abcdefghijklmnopqrstuvwxyz1234567890"


@pytest.fixture
def stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def logger(stream: io.StringIO) -> logging.Logger:
    return configure_logging("DEBUG", secrets=[TOKEN], stream=stream)


def test_token_in_message_is_redacted(logger: logging.Logger, stream: io.StringIO) -> None:
    logger.info("calling Jira with token %s", TOKEN)
    output = stream.getvalue()
    assert TOKEN not in output
    assert REDACTED in output


def test_token_in_format_args_is_redacted(logger: logging.Logger, stream: io.StringIO) -> None:
    logger.warning("request failed: %s", f"Authorization: Basic {TOKEN}")
    assert TOKEN not in stream.getvalue()


def test_token_in_dict_args_is_redacted(logger: logging.Logger, stream: io.StringIO) -> None:
    logger.info("headers %(auth)s", {"auth": f"Bearer {TOKEN}"})
    assert TOKEN not in stream.getvalue()


def test_token_in_traceback_is_redacted(logger: logging.Logger, stream: io.StringIO) -> None:
    try:
        raise RuntimeError(f"boom with Authorization: Basic {TOKEN}")
    except RuntimeError:
        logger.exception("scan failed")
    assert TOKEN not in stream.getvalue()


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Basic a2F5QGV4YW1wbGUuY29tOnNlY3JldA==",
        'authorization="Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"',
        "AUTHORIZATION: basic QUJDREVGR0hJSktMTU5PUA==",
        "api_token=some-unregistered-value",
        "JIRA_API_TOKEN: another-unregistered-value",
    ],
)
def test_authorization_headers_are_redacted_even_when_the_secret_is_unknown(line: str) -> None:
    """An httpx request dump can carry a header we never registered as a secret."""
    scrubbed = RedactingFilter(secrets=[]).scrub(line)
    assert REDACTED in scrubbed
    for leak in ("a2F5", "eyJhbGciOi", "QUJDREVG", "some-unregistered", "another-unregistered"):
        assert leak not in scrubbed


def test_ordinary_text_is_left_alone() -> None:
    message = "Scanned 482 issues in project ITS and found 17 high-risk findings"
    assert RedactingFilter(secrets=[TOKEN]).scrub(message) == message


def test_short_secrets_are_ignored_to_avoid_mangling_everything() -> None:
    """A 1-3 character 'secret' would redact half the log; refuse to register it."""
    assert RedactingFilter(secrets=["a"]).scrub("a normal sentence") == "a normal sentence"


def test_configure_logging_is_idempotent(stream: io.StringIO) -> None:
    for _ in range(3):
        logger = configure_logging("INFO", secrets=[TOKEN], stream=stream)
    assert len(logger.handlers) == 1
