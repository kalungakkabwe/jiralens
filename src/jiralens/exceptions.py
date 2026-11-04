"""Exception hierarchy for JiraLens.

Every message raised here is shown to an administrator, so messages must be
actionable and must never contain a credential. Tests assert this.
"""

from __future__ import annotations


class JiraLensError(Exception):
    """Base class for every JiraLens error.

    ``exit_code`` is used by the CLI so shell callers can branch on failure type.
    """

    exit_code: int = 1


class ConfigurationError(JiraLensError):
    """Settings are missing or invalid."""

    exit_code = 2


class AuthenticationError(JiraLensError):
    """Jira rejected the supplied credentials (HTTP 401)."""

    exit_code = 3


class JiraPermissionError(JiraLensError):
    """The authenticated account lacks permission for the request (HTTP 403).

    Named with a ``Jira`` prefix so it does not shadow the builtin ``PermissionError``.
    """

    exit_code = 4


class ProjectNotFoundError(JiraLensError):
    """The project key does not exist, or is not visible to this account (HTTP 404)."""

    exit_code = 5


class JiraRateLimitError(JiraLensError):
    """Jira throttled the client and retries were exhausted (HTTP 429)."""

    exit_code = 6


class JiraApiError(JiraLensError):
    """Jira returned an unexpected response, or the response was malformed."""

    exit_code = 7


class ReportGenerationError(JiraLensError):
    """A report could not be written."""

    exit_code = 8


class ReadOnlyViolationError(JiraLensError):
    """A request was attempted that is not on the read-only endpoint allowlist.

    This is a programming error, not a runtime condition: it means code tried to
    reach an endpoint or HTTP method that JiraLens must never use.
    """

    exit_code = 9


__all__ = [
    "AuthenticationError",
    "ConfigurationError",
    "JiraApiError",
    "JiraLensError",
    "JiraPermissionError",
    "JiraRateLimitError",
    "ProjectNotFoundError",
    "ReadOnlyViolationError",
    "ReportGenerationError",
]
