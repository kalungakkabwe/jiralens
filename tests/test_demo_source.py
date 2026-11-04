"""The synthetic dataset.

These tests do double duty: they prove the demo source works, and they pin the
boundary-case corpus that the Phase 2 checks will be written against.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jiralens.models import IssueRecord, ScanConfig, ScanContext
from jiralens.sources import IssueSource
from jiralens.sources.demo import DemoIssueSource
from tests.conftest import FROZEN_NOW, LUSAKA


@pytest.fixture
def source() -> DemoIssueSource:
    return DemoIssueSource(now=FROZEN_NOW)


@pytest.fixture
def issues(source: DemoIssueSource, config: ScanConfig) -> list[IssueRecord]:
    return source.fetch_issues(config)


@pytest.fixture
def by_key(issues: list[IssueRecord]) -> dict[str, IssueRecord]:
    return {i.key: i for i in issues}


class TestProtocolConformance:
    def test_demo_source_satisfies_the_issue_source_protocol(self, source: DemoIssueSource) -> None:
        assert isinstance(source, IssueSource)

    def test_naive_now_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            DemoIssueSource(now=datetime(2026, 11, 4))  # noqa: DTZ001


class TestDatasetShape:
    def test_default_scan_returns_only_open_issues(self, issues: list[IssueRecord]) -> None:
        assert issues
        assert all(i.is_open for i in issues)

    def test_custom_jql_returns_the_whole_dataset_including_done(
        self, source: DemoIssueSource
    ) -> None:
        all_issues = source.fetch_issues(ScanConfig(project_key="DEMO", jql="project = DEMO"))
        assert any(not i.is_open for i in all_issues)
        assert len(all_issues) > len(source.fetch_issues(ScanConfig(project_key="DEMO")))

    def test_every_timestamp_is_timezone_aware(self, issues: list[IssueRecord]) -> None:
        for issue in issues:
            assert issue.created_at.tzinfo is not None
            assert issue.updated_at.tzinfo is not None

    def test_keys_are_unique(self, issues: list[IssueRecord]) -> None:
        keys = [i.key for i in issues]
        assert len(keys) == len(set(keys))

    def test_updated_is_never_before_created(self, issues: list[IssueRecord]) -> None:
        for issue in issues:
            assert issue.updated_at >= issue.created_at, issue.key

    def test_dataset_covers_every_condition(self, issues: list[IssueRecord]) -> None:
        ctx = ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO"))
        assert any(i.assignee_account_id is None for i in issues), "no unassigned issue"
        assert any(i.due_date is not None and i.due_date < ctx.local_today for i in issues), (
            "no overdue issue"
        )
        assert any(ctx.calendar_days_since(i.updated_at) > 30 for i in issues), "no stale issue"
        assert any(
            (i.priority or "") in {"Highest", "High"} and ctx.calendar_days_since(i.updated_at) > 7
            for i in issues
        ), "no high-priority stale issue"
        assert any(i.priority is None for i in issues), "no null-priority issue"


class TestRelativeDatesAreRebased:
    """The dataset must stay meaningful in any future year."""

    def test_ages_are_measured_from_the_injected_now(self, by_key: dict[str, IssueRecord]) -> None:
        ctx = ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO"))
        assert ctx.calendar_days_since(by_key["DEMO-53"].updated_at) == 30
        assert ctx.calendar_days_since(by_key["DEMO-54"].updated_at) == 29
        assert ctx.calendar_days_since(by_key["DEMO-55"].updated_at) == 31

    def test_moving_the_clock_a_year_keeps_the_same_relative_ages(self, config: ScanConfig) -> None:
        later = FROZEN_NOW + timedelta(days=365)
        issues = {i.key: i for i in DemoIssueSource(now=later).fetch_issues(config)}
        ctx = ScanContext(now=later, config=config)
        assert ctx.calendar_days_since(issues["DEMO-53"].updated_at) == 30
        assert ctx.calendar_days_since(issues["DEMO-55"].updated_at) == 31

    def test_due_dates_are_rendered_as_bare_dates(self, by_key: dict[str, IssueRecord]) -> None:
        ctx = ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO"))
        assert by_key["DEMO-52"].due_date == ctx.local_today

    def test_malformed_token_is_reported(self) -> None:
        from jiralens.exceptions import JiraApiError
        from jiralens.sources.demo import _rebase

        with pytest.raises(JiraApiError, match="Malformed demo date token"):
            _rebase("@tomorrow", FROZEN_NOW, date_only=False)


class TestBoundaryCases:
    """Each of these exists so a Phase 2 check can be proven right or wrong."""

    def test_done_but_overdue_issue_is_excluded_from_the_open_scan(
        self, by_key: dict[str, IssueRecord]
    ) -> None:
        assert "DEMO-51" not in by_key

    def test_issue_due_exactly_today_is_present_and_not_yet_overdue(
        self, by_key: dict[str, IssueRecord]
    ) -> None:
        ctx = ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO"))
        issue = by_key["DEMO-52"]
        assert issue.due_date == ctx.local_today
        assert not (issue.due_date < ctx.local_today)

    def test_csv_injection_fixture_is_present(self, by_key: dict[str, IssueRecord]) -> None:
        assert by_key["DEMO-59"].summary.startswith("=")

    def test_html_escaping_fixture_is_present(self, by_key: dict[str, IssueRecord]) -> None:
        assert "<script>" in by_key["DEMO-60"].summary

    def test_excluded_status_fixture_is_present(self, by_key: dict[str, IssueRecord]) -> None:
        assert by_key["DEMO-49"].status == "Waiting for Customer"


class TestStatusHistories:
    def test_history_is_returned_only_for_requested_keys(self, source: DemoIssueSource) -> None:
        histories = source.fetch_status_history(["DEMO-43", "DEMO-44"])
        assert set(histories) == {"DEMO-43", "DEMO-44"}

    def test_issues_without_transitions_are_absent_from_the_mapping(
        self, source: DemoIssueSource
    ) -> None:
        """Callers must fall back to created_at, not assume a zero age."""
        assert source.fetch_status_history(["DEMO-56", "DEMO-57"]) == {}

    def test_long_history_is_sorted_and_the_latest_change_wins(
        self, source: DemoIssueSource
    ) -> None:
        """DEMO-48 is the multi-page trap: 8 transitions, newest last."""
        changes = source.fetch_status_history(["DEMO-48"])["DEMO-48"]
        assert len(changes) == 8
        assert changes == sorted(changes, key=lambda c: c.changed_at)
        ctx = ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO"))
        assert ctx.calendar_days_since(changes[-1].changed_at) == 32

    def test_reopened_issue_history_ends_back_in_progress(self, source: DemoIssueSource) -> None:
        changes = source.fetch_status_history(["DEMO-58"])["DEMO-58"]
        assert [c.to_status for c in changes] == ["In Progress", "Done", "In Progress"]

    def test_every_change_timestamp_is_aware(self, source: DemoIssueSource) -> None:
        for changes in source.fetch_status_history([f"DEMO-{n}" for n in range(1, 62)]).values():
            for change in changes:
                assert change.changed_at.tzinfo is not None

    def test_requesting_nothing_returns_nothing(self, source: DemoIssueSource) -> None:
        assert source.fetch_status_history([]) == {}


class TestDatasetRealism:
    """Invariants that keep the demo credible rather than merely well-formed."""

    INITIAL_STATUS = "To Do"

    def test_every_issue_outside_the_initial_status_has_a_transition(
        self, source: DemoIssueSource, issues: list[IssueRecord]
    ) -> None:
        """An issue cannot reach 'In Progress' without transitioning there.

        Without this, such an issue falls back to its creation date and the status-age
        check reports an age that could not occur in real Jira.
        """
        histories = source.fetch_status_history([i.key for i in issues])
        missing = [
            i.key for i in issues if i.status != self.INITIAL_STATUS and not histories.get(i.key)
        ]
        assert missing == [], f"non-initial status with no history: {missing}"

    def test_initial_status_issues_may_legitimately_have_no_history(
        self, source: DemoIssueSource, issues: list[IssueRecord]
    ) -> None:
        """This is the real-world case the created-date fallback exists for."""
        histories = source.fetch_status_history([i.key for i in issues])
        untouched = [
            i.key for i in issues if i.status == self.INITIAL_STATUS and not histories.get(i.key)
        ]
        assert untouched, "no fixture exercises the created-date fallback"

    def test_no_transition_predates_the_issue_it_belongs_to(
        self, source: DemoIssueSource, issues: list[IssueRecord]
    ) -> None:
        histories = source.fetch_status_history([i.key for i in issues])
        for issue in issues:
            for change in histories.get(issue.key, []):
                assert change.changed_at >= issue.created_at, issue.key


class TestTimezoneIndependence:
    def test_the_same_dataset_loads_under_a_dst_observing_zone(self) -> None:
        chicago_cfg = ScanConfig(project_key="DEMO", timezone="America/Chicago")
        now = datetime(2026, 11, 4, 10, 30, tzinfo=LUSAKA)
        issues = DemoIssueSource(now=now).fetch_issues(chicago_cfg)
        assert issues
        assert all(i.created_at.tzinfo is not None for i in issues)
