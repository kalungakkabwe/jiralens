"""The read-only Jira client, against mocked HTTP.

No test here touches a real Jira site. The most important one asserts that a complete
scan never emits a write verb — that is the evidence behind the read-only claim, and
a docstring is not.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from jiralens.config import Settings
from jiralens.exceptions import (
    AuthenticationError,
    JiraApiError,
    JiraPermissionError,
    JiraRateLimitError,
    ProjectNotFoundError,
    ReadOnlyViolationError,
)
from jiralens.jira.client import (
    READ_ONLY_ENDPOINTS,
    JiraClient,
    _assert_allowed,
    _retry_after_seconds,
)

SITE = "https://sandbox.atlassian.net"
GATEWAY = "https://api.atlassian.com"
CLOUD_ID = "00000000-1111-2222-3333-444444444444"
TOKEN = "fake-test-token-not-a-real-credential-abcdef"


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "jira_base_url": SITE,
        "jira_email": "kay@example.com",
        "jira_api_token": TOKEN,
        "max_retry_attempts": 4,
    }
    return Settings(**{**base, **kw})


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def client(slept: list[float]) -> JiraClient:
    return JiraClient(settings(), sleep=slept.append)


# ---------------------------------------------------------------------------
# Read-only enforcement
# ---------------------------------------------------------------------------


class TestReadOnlyEnforcement:
    @pytest.mark.parametrize("verb", ["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def test_write_verbs_are_refused_before_any_request_is_made(self, verb: str) -> None:
        with pytest.raises(ReadOnlyViolationError, match="read-only"):
            _assert_allowed(verb, "/rest/api/3/myself")

    @pytest.mark.parametrize(
        "path",
        [
            "/rest/api/3/issue",
            "/rest/api/3/issue/ITS-1",
            "/rest/api/3/issue/ITS-1/transitions",
            "/rest/api/3/issue/ITS-1/comment",
            "/rest/api/3/issue/bulk",
            "/rest/api/3/project",
            "/rest/api/3/search",
        ],
        ids=lambda p: p,
    )
    def test_endpoints_off_the_allowlist_are_refused(self, path: str) -> None:
        with pytest.raises(ReadOnlyViolationError, match="allowlist"):
            _assert_allowed("POST", path)

    @pytest.mark.parametrize(
        ("verb", "path"),
        [
            ("GET", "/rest/api/3/myself"),
            ("GET", "/rest/api/3/serverInfo"),
            ("GET", "/rest/api/3/project/ITS"),
            ("POST", "/rest/api/3/search/jql"),
            ("POST", "/rest/api/3/changelog/bulkfetch"),
        ],
    )
    def test_the_five_permitted_operations_are_allowed(self, verb: str, path: str) -> None:
        _assert_allowed(verb, path)  # must not raise

    def test_a_get_on_a_post_only_endpoint_is_refused(self) -> None:
        """The allowlist pairs method with path; neither alone is sufficient."""
        with pytest.raises(ReadOnlyViolationError):
            _assert_allowed("GET", "/rest/api/3/search/jql")

    def test_the_allowlist_holds_exactly_five_operations(self) -> None:
        """Widening the surface must be a deliberate, reviewable change."""
        assert len(READ_ONLY_ENDPOINTS) == 5

    def test_the_client_exposes_no_write_methods(self) -> None:
        public = {n for n in dir(JiraClient) if not n.startswith("_")}
        for forbidden in (
            "create_issue",
            "update_issue",
            "edit_issue",
            "transition_issue",
            "add_comment",
            "delete_issue",
            "assign_issue",
            "post",
            "put",
            "delete",
        ):
            assert forbidden not in public, forbidden

    @respx.mock
    def test_a_full_scan_emits_no_write_verb(self, client: JiraClient) -> None:
        """The evidence behind the read-only claim."""
        writes = respx.route(method__in=["PUT", "PATCH", "DELETE"]).mock(
            return_value=httpx.Response(200, json={})
        )
        respx.get(f"{SITE}/rest/api/3/project/ITS").mock(
            return_value=httpx.Response(200, json={"id": "1", "key": "ITS", "name": "IT"})
        )
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )

        client.get_project("ITS")
        client.search_issues("project = ITS", ["status"])
        client.get_status_changelogs(["10001"])

        assert not writes.called


# ---------------------------------------------------------------------------
# Authentication and routing
# ---------------------------------------------------------------------------


class TestBaseUrlRouting:
    def test_an_unscoped_token_calls_the_site_directly(self) -> None:
        assert settings().api_base_url == SITE

    def test_a_scoped_token_calls_the_gateway(self) -> None:
        assert settings(jira_cloud_id=CLOUD_ID).api_base_url == (f"{GATEWAY}/ex/jira/{CLOUD_ID}")

    def test_issue_links_always_use_the_site_url_not_the_gateway(self) -> None:
        """A gateway URL in a report would produce dead links for every finding."""
        scoped = settings(jira_cloud_id=CLOUD_ID)
        assert scoped.site_url == SITE
        assert JiraClient(scoped).site_url == SITE

    @respx.mock
    def test_a_scoped_client_actually_requests_the_gateway_path(self) -> None:
        route = respx.get(f"{GATEWAY}/ex/jira/{CLOUD_ID}/rest/api/3/myself").mock(
            return_value=httpx.Response(200, json={"accountId": "a1", "displayName": "Kay"})
        )
        JiraClient(settings(jira_cloud_id=CLOUD_ID)).test_connection()
        assert route.called

    def test_a_non_uuid_cloud_id_is_rejected_with_guidance(self) -> None:
        with pytest.raises(ValueError, match="tenant_info"):
            settings(jira_cloud_id="sandbox.atlassian.net")


class TestConnectionTest:
    @respx.mock
    def test_it_returns_the_authenticated_user(self, client: JiraClient) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            return_value=httpx.Response(
                200,
                json={
                    "accountId": "5b10",
                    "displayName": "Kay Kabwe",
                    "emailAddress": "kay@example.com",
                    "accountType": "atlassian",
                },
            )
        )
        user = client.test_connection()
        assert user.account_id == "5b10"
        assert user.display_name == "Kay Kabwe"
        assert user.account_type == "atlassian"

    @respx.mock
    def test_a_response_without_an_account_is_an_error(self, client: JiraClient) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(JiraApiError, match="did not return an account"):
            client.test_connection()

    @respx.mock
    def test_the_request_carries_basic_auth(self, client: JiraClient) -> None:
        route = respx.get(f"{SITE}/rest/api/3/myself").mock(
            return_value=httpx.Response(200, json={"accountId": "a", "displayName": "d"})
        )
        client.test_connection()
        assert route.calls.last.request.headers["authorization"].startswith("Basic ")


class TestStatusMapping:
    @respx.mock
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (400, JiraApiError),
            (401, AuthenticationError),
            (403, JiraPermissionError),
            (404, ProjectNotFoundError),
        ],
    )
    def test_permanent_failures_map_to_their_own_exception(
        self, client: JiraClient, code: int, expected: type[Exception]
    ) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(code))
        with pytest.raises(expected):
            client.test_connection()

    @respx.mock
    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_permanent_failures_are_not_retried(
        self, client: JiraClient, slept: list[float], code: int
    ) -> None:
        route = respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(code))
        with pytest.raises(Exception, match=r".*"):
            client.test_connection()
        assert route.call_count == 1
        assert slept == []

    @respx.mock
    def test_the_401_message_explains_the_scoped_token_trap(self, client: JiraClient) -> None:
        """Mismatching token type and base URL yields a 401 that looks like a bad key."""
        respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(401))
        with pytest.raises(AuthenticationError, match="JIRA_CLOUD_ID"):
            client.test_connection()

    @respx.mock
    def test_the_403_message_names_the_permission_needed(self, client: JiraClient) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(403))
        with pytest.raises(JiraPermissionError, match="Browse Projects"):
            client.test_connection()


class TestCredentialsNeverLeak:
    @respx.mock
    @pytest.mark.parametrize("code", [400, 401, 403, 404, 418])
    def test_no_error_message_contains_the_token(self, client: JiraClient, code: int) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            return_value=httpx.Response(code, text=f"leaked {TOKEN}")
        )
        with pytest.raises(Exception) as exc:
            client.test_connection()
        assert TOKEN not in str(exc.value)

    @respx.mock
    def test_a_malformed_body_error_does_not_echo_the_body(self, client: JiraClient) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            return_value=httpx.Response(200, text=f"not json {TOKEN}")
        )
        with pytest.raises(JiraApiError) as exc:
            client.test_connection()
        assert TOKEN not in str(exc.value)


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestRetry:
    @respx.mock
    def test_a_503_then_200_succeeds(self, client: JiraClient, slept: list[float]) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"accountId": "a", "displayName": "d"}),
            ]
        )
        assert client.test_connection().account_id == "a"
        assert len(slept) == 1

    @respx.mock
    def test_429_honours_retry_after(self, client: JiraClient, slept: list[float]) -> None:
        """Atlassian asks clients to respect Retry-After rather than guessing."""
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, json={"accountId": "a", "displayName": "d"}),
            ]
        )
        client.test_connection()
        assert slept == [7.0]

    @respx.mock
    def test_retry_after_is_used_verbatim_without_jitter(
        self, client: JiraClient, slept: list[float]
    ) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(429, headers={"Retry-After": "3"}),
                httpx.Response(200, json={"accountId": "a", "displayName": "d"}),
            ]
        )
        client.test_connection()
        assert slept == [2.0, 3.0]

    @respx.mock
    def test_an_http_date_retry_after_falls_back_to_backoff(
        self, client: JiraClient, slept: list[float]
    ) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                httpx.Response(200, json={"accountId": "a", "displayName": "d"}),
            ]
        )
        client.test_connection()
        assert len(slept) == 1
        assert 1.0 <= slept[0] <= 1.25  # initial backoff plus jitter

    @respx.mock
    def test_exhausted_throttling_raises_a_rate_limit_error(self, client: JiraClient) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"})
        )
        with pytest.raises(JiraRateLimitError, match="throttling"):
            client.test_connection()

    @respx.mock
    def test_attempts_are_capped(self, client: JiraClient) -> None:
        route = respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(503))
        with pytest.raises(JiraApiError):
            client.test_connection()
        assert route.call_count == 4  # max_retry_attempts

    @respx.mock
    def test_backoff_grows_and_is_capped(self, slept: list[float]) -> None:
        client = JiraClient(settings(max_retry_attempts=6), sleep=slept.append)
        respx.get(f"{SITE}/rest/api/3/myself").mock(return_value=httpx.Response(503))
        with pytest.raises(JiraApiError):
            client.test_connection()
        assert slept == sorted(slept)
        assert all(d <= 30.0 * 1.25 for d in slept)

    @respx.mock
    def test_a_timeout_is_retried_then_reported_clearly(
        self, client: JiraClient, slept: list[float]
    ) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(side_effect=httpx.ReadTimeout("too slow"))
        with pytest.raises(JiraApiError, match="timed out"):
            client.test_connection()
        assert len(slept) == 3  # retried on each attempt but the last

    @respx.mock
    def test_a_connection_error_is_not_retried_forever(self, client: JiraClient) -> None:
        respx.get(f"{SITE}/rest/api/3/myself").mock(
            side_effect=httpx.ConnectError("no route to host")
        )
        with pytest.raises(JiraApiError, match="could not reach Jira"):
            client.test_connection()


class TestRetryAfterParsing:
    @pytest.mark.parametrize(("raw", "expected"), [("5", 5.0), ("0", 0.0), (" 12 ", 12.0)])
    def test_numeric_values(self, raw: str, expected: float) -> None:
        response = httpx.Response(429, headers={"Retry-After": raw})
        assert _retry_after_seconds(response) == expected

    @pytest.mark.parametrize("raw", ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", ""])
    def test_unparseable_values_yield_none(self, raw: str) -> None:
        response = httpx.Response(429, headers={"Retry-After": raw} if raw else {})
        assert _retry_after_seconds(response) is None

    def test_a_negative_value_is_floored_at_zero(self) -> None:
        response = httpx.Response(429, headers={"Retry-After": "-5"})
        assert _retry_after_seconds(response) == 0.0
