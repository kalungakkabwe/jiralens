"""The read-only Jira Cloud client.

Read-only is enforced here, not promised in a docstring. Every request passes through
``_request``, which refuses any method outside GET/POST and any path not on
``READ_ONLY_ENDPOINTS``. There is no ``create_issue``, ``update_issue``,
``transition_issue``, ``add_comment`` or ``delete_issue`` method, and a test walks the
client's public surface during a full scan asserting no PUT, PATCH or DELETE is ever
emitted.

Two of the five endpoints are POST. They are retrieval operations — Jira uses POST so
a long JQL string or a thousand issue ids fit in a body rather than a URL.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from jiralens.config import Settings
from jiralens.exceptions import (
    AuthenticationError,
    JiraApiError,
    JiraPermissionError,
    JiraRateLimitError,
    ProjectNotFoundError,
    ReadOnlyViolationError,
)
from jiralens.logging_config import get_logger

logger = get_logger("jiralens.jira.client")

#: Every endpoint JiraLens is permitted to reach, as (method, path pattern).
#: Adding a row here is the only way to reach a new endpoint, which makes widening
#: the surface a deliberate, reviewable act rather than an accident.
READ_ONLY_ENDPOINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"^/rest/api/3/myself$")),
    ("GET", re.compile(r"^/rest/api/3/serverInfo$")),
    ("GET", re.compile(r"^/rest/api/3/project/[A-Za-z0-9_]+$")),
    ("POST", re.compile(r"^/rest/api/3/search/jql$")),
    ("POST", re.compile(r"^/rest/api/3/changelog/bulkfetch$")),
)

#: Status codes worth trying again. Everything else is a permanent answer.
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0

#: Jira caps changelog bulkfetch at 1,000 issue ids per request. Verify against the
#: current docs if a large scan starts failing — this endpoint was experimental for a
#: while and the cap has moved before.
MAX_CHANGELOG_BATCH = 1000

#: The endpoint accepts up to ten field ids. JiraLens needs exactly one.
CHANGELOG_FIELD_IDS = ("status",)

#: Page size for issue search.
SEARCH_PAGE_SIZE = 100


@dataclass(frozen=True)
class AuthenticatedUser:
    account_id: str
    display_name: str
    email: str | None
    account_type: str | None


@dataclass(frozen=True)
class ServerInfo:
    base_url: str | None
    server_title: str | None
    version: str | None


@dataclass(frozen=True)
class JiraProject:
    id: str
    key: str
    name: str


def _assert_allowed(method: str, path: str) -> None:
    """Refuse anything not on the allowlist. Raised failures are programming errors."""
    verb = method.upper()
    if verb not in {"GET", "POST"}:
        raise ReadOnlyViolationError(
            f"JiraLens attempted a {verb} request to {path}. The client is read-only "
            "and may only issue GET or POST."
        )
    for allowed_verb, pattern in READ_ONLY_ENDPOINTS:
        if verb == allowed_verb and pattern.match(path):
            return
    raise ReadOnlyViolationError(
        f"{verb} {path} is not on the read-only endpoint allowlist. "
        "Add it to READ_ONLY_ENDPOINTS only if it is genuinely a retrieval operation."
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Honour Retry-After when Jira sends one. Atlassian asks clients to respect it."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        # Retry-After may be an HTTP date. Falling back to our own backoff is safer
        # than parsing dates against a possibly skewed local clock.
        return None


class JiraClient:
    """A deliberately small, read-only surface over the Jira Cloud REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_base, email, token = self._credentials(settings)
        self._settings = settings
        self._api_base = api_base
        self._site_url = settings.site_url or api_base
        self._sleep = sleep
        self._max_attempts = settings.max_retry_attempts
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=api_base,
            auth=(email, token.get_secret_value()),
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={"Accept": "application/json", "User-Agent": "JiraLens"},
            follow_redirects=False,
        )

    @staticmethod
    def _credentials(settings: Settings) -> tuple[str, str, Any]:
        settings.require_live()
        api_base = settings.api_base_url
        assert api_base is not None  # require_live guarantees base_url is set
        assert settings.jira_email is not None
        assert settings.jira_api_token is not None
        return api_base, settings.jira_email, settings.jira_api_token

    # -- lifecycle ---------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def site_url(self) -> str:
        """The human-facing site URL, used to build issue links in reports."""
        return self._site_url

    # -- transport ---------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        """Issue one allowlisted request, retrying transient failures.

        Raises:
            ReadOnlyViolationError: the request is not on the allowlist.
            AuthenticationError, JiraPermissionError, ProjectNotFoundError: permanent
                answers that retrying cannot fix.
            JiraRateLimitError: throttled and out of attempts.
            JiraApiError: anything else, including a malformed body.
        """
        _assert_allowed(method, path)

        backoff = INITIAL_BACKOFF_SECONDS
        last_error: str = "no response"

        for attempt in range(1, self._max_attempts + 1):
            try:
                started = time.perf_counter()
                response = self._client.request(method, path, params=params, json=json)
                elapsed = time.perf_counter() - started
                logger.debug(
                    "%s %s -> %s in %.0fms", method, path, response.status_code, elapsed * 1000
                )
            except httpx.TimeoutException as exc:
                last_error = f"request timed out after {self._settings.request_timeout_seconds}s"
                logger.warning("%s %s timed out (attempt %s)", method, path, attempt)
                if attempt == self._max_attempts:
                    raise JiraApiError(f"{method} {path}: {last_error}") from exc
                backoff = self._wait(backoff, None)
                continue
            except httpx.HTTPError as exc:
                # Connection-level failures carry no credentials, but the message is
                # rebuilt rather than interpolated so nothing unexpected rides along.
                raise JiraApiError(
                    f"{method} {path}: could not reach Jira ({type(exc).__name__})"
                ) from exc

            if response.status_code < 300:
                return self._parse(response, method, path)

            self._raise_for_permanent_status(response, path)

            if response.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                if attempt == self._max_attempts:
                    break
                backoff = self._wait(backoff, _retry_after_seconds(response))
                continue

            raise JiraApiError(f"{method} {path}: unexpected HTTP {response.status_code}")

        if last_error == "HTTP 429":
            raise JiraRateLimitError(
                f"{method} {path}: Jira is throttling this client and "
                f"{self._max_attempts} attempts were exhausted. Try a narrower JQL, "
                "or wait and run the scan again."
            )
        raise JiraApiError(f"{method} {path}: {last_error} after {self._max_attempts} attempts.")

    def _wait(self, backoff: float, retry_after: float | None) -> float:
        """Sleep, then return the next backoff. Jitter avoids a retry stampede."""
        delay = retry_after if retry_after is not None else backoff
        delay = min(delay, MAX_BACKOFF_SECONDS)
        if retry_after is None:
            delay += random.uniform(0, 0.25 * delay)  # noqa: S311 - not security critical
        logger.info("retrying in %.1fs", delay)
        self._sleep(delay)
        return min(backoff * 2, MAX_BACKOFF_SECONDS)

    @staticmethod
    def _raise_for_permanent_status(response: httpx.Response, path: str) -> None:
        """Map the answers that retrying cannot change. Never echoes the request body."""
        code = response.status_code
        if code == 400:
            raise JiraApiError(
                f"Jira rejected the request to {path} as invalid (HTTP 400). "
                "If a custom JQL was supplied, check its syntax and field names."
            )
        if code == 401:
            raise AuthenticationError(
                "Jira rejected the supplied credentials. Check JIRA_EMAIL and "
                "JIRA_API_TOKEN in your local .env, and confirm the token has not "
                "expired. If you are using a scoped token, JIRA_CLOUD_ID must be set; "
                "if you are using an unscoped one, it must be empty."
            )
        if code == 403:
            raise JiraPermissionError(
                "The authenticated account is not permitted to perform this request. "
                "JiraLens needs Browse Projects on the project being audited."
            )
        if code == 404:
            raise ProjectNotFoundError(
                f"Jira could not find {path}. Check the project key, and confirm the "
                "authenticated account can see that project."
            )

    @staticmethod
    def _parse(response: httpx.Response, method: str, path: str) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise JiraApiError(
                f"{method} {path}: Jira returned a response that is not valid JSON."
            ) from exc

    # -- read operations ---------------------------------------------------------

    def test_connection(self) -> AuthenticatedUser:
        """Confirm credentials via GET /rest/api/3/myself."""
        data = self._request("GET", "/rest/api/3/myself")
        account_id = data.get("accountId")
        if not account_id:
            raise JiraApiError("Jira did not return an account for these credentials.")
        return AuthenticatedUser(
            account_id=account_id,
            display_name=data.get("displayName") or "(unknown)",
            email=data.get("emailAddress"),
            account_type=data.get("accountType"),
        )

    def get_server_info(self) -> ServerInfo:
        data = self._request("GET", "/rest/api/3/serverInfo")
        return ServerInfo(
            base_url=data.get("baseUrl"),
            server_title=data.get("serverTitle"),
            version=data.get("version"),
        )

    def get_project(self, project_key: str) -> JiraProject:
        """Validate a project key before scanning, so a typo fails in one call."""
        key = project_key.strip().upper()
        if not key.isalnum() and "_" not in key:
            raise ProjectNotFoundError(f"{project_key!r} is not a valid Jira project key.")
        data = self._request("GET", f"/rest/api/3/project/{key}")
        return JiraProject(
            id=str(data.get("id", "")),
            key=data.get("key") or key,
            name=data.get("name") or key,
        )

    def search_issues(self, jql: str, fields: Sequence[str]) -> list[dict[str, Any]]:
        """Retrieve every issue matching ``jql``.

        Uses the enhanced ``POST /rest/api/3/search/jql`` operation. Note that it
        returns **no total**, so the count is only known once pagination completes,
        and pages must be fetched sequentially because each depends on the previous
        page's token.
        """
        from jiralens.jira.pagination import paginate_tokens

        def fetch(token: str | None) -> dict[str, Any]:
            body: dict[str, Any] = {
                "jql": jql,
                "fields": list(fields),
                "maxResults": SEARCH_PAGE_SIZE,
            }
            if token:
                body["nextPageToken"] = token
            return self._request("POST", "/rest/api/3/search/jql", json=body)

        issues: list[dict[str, Any]] = []
        for page in paginate_tokens(fetch, label="issue search"):
            batch = page.get("issues")
            if batch is None:
                raise JiraApiError("Jira's search response contained no 'issues' array.")
            issues.extend(batch)
        logger.info("retrieved %s issues", len(issues))
        return issues

    def get_status_changelogs(self, issue_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """Retrieve status changelogs, keyed by issue **id**.

        The live response keys histories by ``issueId``, not by key, so the caller is
        responsible for mapping back. Requests are batched to the endpoint's limit and
        each batch is followed through its own pagination.
        """
        from jiralens.jira.pagination import paginate_tokens

        histories: dict[str, list[dict[str, Any]]] = {}
        unique_ids = list(dict.fromkeys(issue_ids))

        for start in range(0, len(unique_ids), MAX_CHANGELOG_BATCH):
            batch = unique_ids[start : start + MAX_CHANGELOG_BATCH]

            def fetch(token: str | None, batch: list[str] = batch) -> dict[str, Any]:
                body: dict[str, Any] = {
                    "issueIdsOrKeys": batch,
                    "fieldIds": list(CHANGELOG_FIELD_IDS),
                    "maxResults": MAX_CHANGELOG_BATCH,
                }
                if token:
                    body["nextPageToken"] = token
                return self._request("POST", "/rest/api/3/changelog/bulkfetch", json=body)

            for page in paginate_tokens(fetch, label="changelog bulkfetch"):
                for entry in page.get("issueChangeLogs", []):
                    issue_id = str(entry.get("issueId", ""))
                    if not issue_id:
                        continue
                    # Extend rather than replace: a single issue's history can span
                    # pages, and taking only the last page would silently lose the
                    # transitions that determine current-status age.
                    histories.setdefault(issue_id, []).extend(entry.get("changeHistories", []))

        logger.info("retrieved changelogs for %s issues", len(histories))
        return histories


__all__ = [
    "CHANGELOG_FIELD_IDS",
    "MAX_CHANGELOG_BATCH",
    "READ_ONLY_ENDPOINTS",
    "RETRYABLE_STATUS",
    "SEARCH_PAGE_SIZE",
    "AuthenticatedUser",
    "JiraClient",
    "JiraProject",
    "ServerInfo",
]
