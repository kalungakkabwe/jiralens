"""The live issue source, end to end against mocked HTTP.

The id-to-key translation is the subtle part: the search response is keyed by key,
the changelog response by id, and getting the mapping wrong attaches one issue's
history to another — producing status ages that are wrong but entirely plausible.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from jiralens.jira.client import MAX_CHANGELOG_BATCH, JiraClient
from jiralens.jira.normalizer import ISSUE_FIELDS
from jiralens.models import ScanConfig
from jiralens.scanner import run_scan
from jiralens.sources import IssueSource
from jiralens.sources.jira_source import JiraIssueSource
from tests.conftest import FROZEN_NOW
from tests.test_client import SITE, settings


def issue(
    n: int, *, status: str = "In Progress", category: str = "indeterminate"
) -> dict[str, Any]:
    return {
        "id": str(10000 + n),
        "key": f"ITS-{n}",
        "fields": {
            "summary": f"Issue {n}",
            "issuetype": {"name": "Task"},
            "project": {"key": "ITS"},
            "status": {"name": status, "statusCategory": {"key": category}},
            "assignee": None,
            "priority": {"name": "Medium"},
            "created": "2026-09-01T08:00:00.000+0200",
            "updated": "2026-09-20T08:00:00.000+0200",
            "duedate": None,
            "resolution": None,
            "resolutiondate": None,
        },
    }


def changelog(n: int, created: str, to: str = "In Progress") -> dict[str, Any]:
    return {
        "issueId": str(10000 + n),
        "changeHistories": [
            {
                "id": str(900 + n),
                "created": created,
                "items": [{"field": "status", "fromString": "To Do", "toString": to}],
            }
        ],
    }


@pytest.fixture
def client() -> JiraClient:
    return JiraClient(settings(), sleep=lambda _s: None)


@pytest.fixture
def source(client: JiraClient) -> JiraIssueSource:
    return JiraIssueSource(client)


@pytest.fixture
def config() -> ScanConfig:
    return ScanConfig(project_key="ITS", timezone="Africa/Lusaka")


def mock_project() -> None:
    respx.get(f"{SITE}/rest/api/3/project/ITS").mock(
        return_value=httpx.Response(200, json={"id": "1", "key": "ITS", "name": "IT Support"})
    )


class TestProtocol:
    def test_it_satisfies_the_issue_source_protocol(self, source: JiraIssueSource) -> None:
        assert isinstance(source, IssueSource)

    def test_it_identifies_itself_on_the_scan_result(self, source: JiraIssueSource) -> None:
        assert source.name == "jira"


class TestFetchIssues:
    @respx.mock
    def test_it_validates_the_project_before_searching(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        """A mistyped key must fail fast, not look like a healthy empty project."""
        project = respx.get(f"{SITE}/rest/api/3/project/ITS").mock(return_value=httpx.Response(404))
        search = respx.post(f"{SITE}/rest/api/3/search/jql")
        from jiralens.exceptions import ProjectNotFoundError

        with pytest.raises(ProjectNotFoundError):
            source.fetch_issues(config)
        assert project.called
        assert not search.called

    @respx.mock
    def test_it_requests_only_the_fields_the_checks_need(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        mock_project()
        route = respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        source.fetch_issues(config)
        body = route.calls.last.request.read().decode()
        assert '"fields"' in body
        for sensitive in ("description", "comment", "attachment", "worklog"):
            assert sensitive not in body
        import json

        assert json.loads(body)["fields"] == list(ISSUE_FIELDS)

    @respx.mock
    def test_it_sends_the_configs_jql(self, source: JiraIssueSource, config: ScanConfig) -> None:
        import json

        mock_project()
        route = respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        source.fetch_issues(config)
        assert json.loads(route.calls.last.request.read())["jql"] == config.default_jql()

    @respx.mock
    def test_custom_jql_skips_project_validation(self, source: JiraIssueSource) -> None:
        project = respx.get(f"{SITE}/rest/api/3/project/ITS")
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        source.fetch_issues(ScanConfig(project_key="ITS", jql="project = ITS AND x = y"))
        assert not project.called

    @respx.mock
    def test_issues_are_normalised_with_the_site_url(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        issues = source.fetch_issues(config)
        assert issues[0].url == f"{SITE}/browse/ITS-1"

    @respx.mock
    def test_every_page_is_collected(self, source: JiraIssueSource, config: ScanConfig) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            side_effect=[
                httpx.Response(200, json={"issues": [issue(1), issue(2)], "nextPageToken": "t1"}),
                httpx.Response(200, json={"issues": [issue(3)], "nextPageToken": "t2"}),
                httpx.Response(200, json={"issues": [issue(4)]}),
            ]
        )
        keys = [i.key for i in source.fetch_issues(config)]
        assert keys == ["ITS-1", "ITS-2", "ITS-3", "ITS-4"]

    @respx.mock
    def test_a_response_without_an_issues_array_is_an_error(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        from jiralens.exceptions import JiraApiError

        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"total": 5})
        )
        with pytest.raises(JiraApiError, match="no 'issues' array"):
            source.fetch_issues(config)


class TestStatusHistories:
    @respx.mock
    def test_histories_are_translated_from_id_to_key(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        """The response is keyed by issueId; the engine works in keys."""
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1), issue(2)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(
                200,
                json={
                    "issueChangeLogs": [
                        changelog(1, "2026-09-10T08:00:00.000+0200", "In Progress"),
                        changelog(2, "2026-09-15T08:00:00.000+0200", "Blocked"),
                    ]
                },
            )
        )
        source.fetch_issues(config)
        histories = source.fetch_status_history(["ITS-1", "ITS-2"])
        assert set(histories) == {"ITS-1", "ITS-2"}
        assert histories["ITS-1"][-1].to_status == "In Progress"
        assert histories["ITS-2"][-1].to_status == "Blocked"

    @respx.mock
    def test_it_sends_issue_ids_not_keys(self, source: JiraIssueSource, config: ScanConfig) -> None:
        import json

        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        route = respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )
        source.fetch_issues(config)
        source.fetch_status_history(["ITS-1"])
        body = json.loads(route.calls.last.request.read())
        assert body["issueIdsOrKeys"] == ["10001"]
        assert body["fieldIds"] == ["status"]

    @respx.mock
    def test_a_history_spanning_pages_is_merged_not_replaced(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        """Taking only the last page would lose the transition that sets status age."""
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "issueChangeLogs": [changelog(1, "2026-09-05T08:00:00.000+0200", "A")],
                        "nextPageToken": "t1",
                    },
                ),
                httpx.Response(
                    200,
                    json={"issueChangeLogs": [changelog(1, "2026-09-25T08:00:00.000+0200", "B")]},
                ),
            ]
        )
        source.fetch_issues(config)
        changes = source.fetch_status_history(["ITS-1"])["ITS-1"]
        assert len(changes) == 2
        assert changes[-1].to_status == "B"  # sorted oldest-first, newest wins

    @respx.mock
    def test_requests_are_batched_at_the_endpoint_limit(self, client: JiraClient) -> None:
        import json

        count = MAX_CHANGELOG_BATCH + 50
        route = respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )
        client.get_status_changelogs([str(i) for i in range(count)])
        assert route.call_count == 2
        first = json.loads(route.calls[0].request.read())
        second = json.loads(route.calls[1].request.read())
        assert len(first["issueIdsOrKeys"]) == MAX_CHANGELOG_BATCH
        assert len(second["issueIdsOrKeys"]) == 50

    @respx.mock
    def test_duplicate_ids_are_sent_once(self, client: JiraClient) -> None:
        import json

        route = respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )
        client.get_status_changelogs(["1", "2", "1", "2", "3"])
        assert json.loads(route.calls.last.request.read())["issueIdsOrKeys"] == ["1", "2", "3"]

    @respx.mock
    def test_a_history_for_an_unknown_issue_is_ignored(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        """Never invent an issue the audit never saw."""
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(
                200,
                json={"issueChangeLogs": [changelog(99, "2026-09-10T08:00:00.000+0200")]},
            )
        )
        source.fetch_issues(config)
        assert source.fetch_status_history(["ITS-1"]) == {}

    @respx.mock
    def test_asking_for_nothing_makes_no_request(self, source: JiraIssueSource) -> None:
        route = respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch")
        assert source.fetch_status_history([]) == {}
        assert not route.called


class TestFullLiveScan:
    """The Phase 7 exit criterion: a complete scan against mocked Jira."""

    @respx.mock
    def test_a_two_page_scan_produces_findings(self, client: JiraClient) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"issues": [issue(1), issue(2)], "nextPageToken": "t1"},
                ),
                httpx.Response(200, json={"issues": [issue(3)]}),
            ]
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(
                200,
                json={"issueChangeLogs": [changelog(1, "2026-09-02T08:00:00.000+0200")]},
            )
        )

        result = run_scan(
            JiraIssueSource(client),
            ScanConfig(project_key="ITS", timezone="Africa/Lusaka"),
            now=FROZEN_NOW,
        )

        assert result.source_name == "jira"
        assert result.issue_count == 3
        assert result.finding_count > 0
        assert result.skipped_checks == []
        assert all("/browse/ITS-" in f.issue_url for f in result.findings)

    @respx.mock
    def test_no_changelog_scan_skips_the_bulkfetch_call_entirely(self, client: JiraClient) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        bulk = respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch")

        result = run_scan(
            JiraIssueSource(client),
            ScanConfig(project_key="ITS", include_changelog=False),
            now=FROZEN_NOW,
        )
        assert not bulk.called
        assert result.skipped_checks == ["status_age"]

    @respx.mock
    def test_a_throttled_scan_recovers_and_completes(self, client: JiraClient) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json={"issues": [issue(1)]}),
            ]
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )
        result = run_scan(JiraIssueSource(client), ScanConfig(project_key="ITS"), now=FROZEN_NOW)
        assert result.issue_count == 1

    @respx.mock
    def test_a_failed_changelog_degrades_rather_than_aborting_the_scan(
        self, client: JiraClient
    ) -> None:
        """A partial audit that names what is missing beats no audit."""
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(return_value=httpx.Response(503))
        result = run_scan(JiraIssueSource(client), ScanConfig(project_key="ITS"), now=FROZEN_NOW)
        assert result.skipped_checks == ["status_age"]
        assert any("could not be retrieved" in w for w in result.warnings)
        assert result.finding_count > 0

    @respx.mock
    def test_reports_generated_from_a_live_scan_carry_no_credentials(
        self, client: JiraClient, tmp_path: Any
    ) -> None:
        from jiralens.reports import write_reports
        from tests.test_client import TOKEN

        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )
        result = run_scan(JiraIssueSource(client), ScanConfig(project_key="ITS"), now=FROZEN_NOW)
        for path in write_reports(result, tmp_path).values():
            text = path.read_text(encoding="utf-8")
            assert TOKEN not in text
            assert "Authorization" not in text


class TestLivePayloadRegression:
    """The first live scan returned a changelog that parsed to nothing, and the
    result was indistinguishable from an untouched backlog. These lock that shut."""

    @staticmethod
    def _live_changelog(n: int) -> dict[str, Any]:
        """A real bulkfetch record: 'created' is epoch milliseconds, not ISO."""
        return {
            "issueId": str(10000 + n),
            "changeHistories": [
                {
                    "id": "10310",
                    "author": {"accountId": "acc-1", "displayName": "Kay"},
                    "created": 1795256864259,
                    "items": [
                        {
                            "field": "status",
                            "fieldtype": "jira",
                            "fieldId": "status",
                            "fromString": "Waiting for support",
                            "toString": "In Progress",
                        }
                    ],
                }
            ],
        }

    @respx.mock
    def test_an_epoch_millisecond_history_reaches_the_engine(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": [self._live_changelog(1)]})
        )
        source.fetch_issues(config)
        histories = source.fetch_status_history(["ITS-1"])
        assert "ITS-1" in histories, "the live payload shape must not be dropped"
        assert histories["ITS-1"][-1].to_status == "In Progress"
        assert source.warnings == []

    @respx.mock
    def test_records_that_all_fail_to_parse_are_disclosed_loudly(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        """Jira handing back histories while the engine sees none must never pass
        for 'these issues never transitioned'."""
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(
                200,
                json={
                    "issueChangeLogs": [
                        {
                            "issueId": "10001",
                            "changeHistories": [{"id": "1", "created": "nonsense"}],
                        }
                    ]
                },
            )
        )
        source.fetch_issues(config)
        assert source.fetch_status_history(["ITS-1"]) == {}
        joined = " ".join(source.warnings)
        assert "could not be read" in joined
        assert "creation date" in joined

    @respx.mock
    def test_that_disclosure_reaches_the_scan_result(self, client: JiraClient) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(
                200,
                json={
                    "issueChangeLogs": [
                        {
                            "issueId": "10001",
                            "changeHistories": [{"id": "1", "created": "nonsense"}],
                        }
                    ]
                },
            )
        )
        result = run_scan(JiraIssueSource(client), ScanConfig(project_key="ITS"), now=FROZEN_NOW)
        joined = " ".join(result.warnings)
        assert "could not be read" in joined
        assert "response shape has changed" in joined

    @respx.mock
    def test_a_history_for_an_issue_outside_the_search_is_disclosed(
        self, source: JiraIssueSource, config: ScanConfig
    ) -> None:
        mock_project()
        respx.post(f"{SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [issue(1)]})
        )
        respx.post(f"{SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": [self._live_changelog(99)]})
        )
        source.fetch_issues(config)
        source.fetch_status_history(["ITS-1"])
        assert any("did not return" in w for w in source.warnings)
