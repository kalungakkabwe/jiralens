"""Check 5 — excessive current-status age.

This is the check with the most ways to be quietly wrong: the fallback when an issue
has never transitioned, the excluded-status list, and the documented divergence from
the nearest JQL equivalent.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jiralens.checks.status_age import StatusAgeCheck
from jiralens.jira.normalizer import normalize_status_changes
from jiralens.models import ScanConfig, ScanContext
from tests.conftest import CHICAGO, FROZEN_NOW
from tests.factories import make_issue


@pytest.fixture
def check() -> StatusAgeCheck:
    return StatusAgeCheck()


def _ctx(days: int = 14, excluded: frozenset[str] | None = None) -> ScanContext:
    config = ScanConfig(
        project_key="DEMO",
        status_age_days=days,
        excluded_statuses=excluded if excluded is not None else frozenset(),
    )
    return ScanContext(now=FROZEN_NOW, config=config)


class TestThresholdBoundary:
    def test_under_the_threshold_is_not_flagged(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status_since_days_ago=13)
        assert check.evaluate([issue], _ctx(14)) == []

    def test_exactly_at_the_threshold_is_not_flagged(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status_since_days_ago=14)
        assert check.evaluate([issue], _ctx(14)) == []

    def test_over_the_threshold_is_flagged(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status_since_days_ago=15)
        findings = check.evaluate([issue], _ctx(14))
        assert len(findings) == 1
        assert findings[0].measured_value == 15
        assert findings[0].threshold_value == 14

    def test_a_long_running_status_reports_the_full_age(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status="In Progress", status_since_days_ago=26)
        finding = check.evaluate([issue], _ctx(14))[0]
        assert finding.measured_value == 26
        assert "remained in 'In Progress' for 26 calendar days" in finding.reason


class TestFallbackWhenNeverTransitioned:
    """With no status history, the age is measured from creation."""

    def test_recently_created_issue_with_no_transitions_is_not_flagged(
        self, check: StatusAgeCheck
    ) -> None:
        issue = make_issue(created_days_ago=3, status_since_days_ago=None)
        assert check.evaluate([issue], _ctx(14)) == []

    def test_old_issue_with_no_transitions_is_flagged_from_its_creation_date(
        self, check: StatusAgeCheck
    ) -> None:
        issue = make_issue(created_days_ago=60, status_since_days_ago=None)
        findings = check.evaluate([issue], _ctx(14))
        assert len(findings) == 1
        assert findings[0].measured_value == 60

    def test_the_reason_says_the_issue_never_moved(self, check: StatusAgeCheck) -> None:
        issue = make_issue(created_days_ago=60, status_since_days_ago=None)
        assert "since it was created" in check.evaluate([issue], _ctx(14))[0].reason

    def test_the_reason_omits_that_phrase_when_the_issue_did_move(
        self, check: StatusAgeCheck
    ) -> None:
        issue = make_issue(created_days_ago=60, status_since_days_ago=20)
        assert "since it was created" not in check.evaluate([issue], _ctx(14))[0].reason

    def test_the_transition_timestamp_wins_over_the_creation_date(
        self, check: StatusAgeCheck
    ) -> None:
        """An issue created 90 days ago that moved 2 days ago is not long-running."""
        issue = make_issue(created_days_ago=90, status_since_days_ago=2)
        assert check.evaluate([issue], _ctx(14)) == []

    def test_documented_divergence_from_the_jql_cross_check(self, check: StatusAgeCheck) -> None:
        """`NOT status CHANGED AFTER -14d` also matches the recent never-moved issue.

        That one issue is the whole expected difference between the JQL cross-check and
        this check. Anything else appearing in a diff is a real bug.
        """
        recent_never_moved = make_issue("DEMO-56", created_days_ago=3, status_since_days_ago=None)
        old_never_moved = make_issue("DEMO-57", created_days_ago=60, status_since_days_ago=None)
        findings = check.evaluate([recent_never_moved, old_never_moved], _ctx(14))
        assert [f.issue_key for f in findings] == ["DEMO-57"]


class TestExcludedStatuses:
    def test_an_excluded_status_is_skipped(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status="Waiting for Customer", status_since_days_ago=90)
        ctx = _ctx(14, excluded=frozenset({"Waiting for Customer"}))
        assert check.evaluate([issue], ctx) == []

    def test_exclusion_matching_is_case_insensitive(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status="Waiting for Customer", status_since_days_ago=90)
        ctx = _ctx(14, excluded=frozenset({"waiting FOR customer"}))
        assert check.evaluate([issue], ctx) == []

    def test_a_non_excluded_status_is_still_flagged(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status="Blocked", status_since_days_ago=90)
        ctx = _ctx(14, excluded=frozenset({"Waiting for Customer"}))
        assert len(check.evaluate([issue], ctx)) == 1

    def test_without_exclusions_a_waiting_status_is_flagged(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status="Waiting for Customer", status_since_days_ago=90)
        assert len(check.evaluate([issue], _ctx(14))) == 1


class TestScope:
    def test_done_issues_are_never_flagged(self, check: StatusAgeCheck) -> None:
        issue = make_issue(status="Done", status_since_days_ago=400)
        assert check.evaluate([issue], _ctx(14)) == []

    def test_the_check_declares_that_it_needs_the_changelog(self, check: StatusAgeCheck) -> None:
        """The orchestrator reads this to decide what to skip under --no-changelog."""
        assert check.requires_changelog is True


class TestSeverity:
    def test_ordinary_priority_is_a_warning(self, check: StatusAgeCheck) -> None:
        issue = make_issue(priority="Medium", status_since_days_ago=30)
        assert check.evaluate([issue], _ctx(14))[0].severity == "warning"

    def test_high_priority_escalates(self, check: StatusAgeCheck) -> None:
        issue = make_issue(priority="Highest", status_since_days_ago=30)
        assert check.evaluate([issue], _ctx(14))[0].severity == "high"


class TestReopenedIssues:
    @staticmethod
    def _reopened_issue() -> object:
        """Created 100 days ago, worked, closed, then reopened 10 days ago."""
        entries = [
            {
                "created": (FROZEN_NOW - timedelta(days=days)).isoformat(),
                "items": [{"field": "status", "fromString": frm, "toString": to}],
            }
            for days, frm, to in [
                (90, "To Do", "In Progress"),
                (60, "In Progress", "Done"),
                (10, "Done", "In Progress"),
            ]
        ]
        changes = normalize_status_changes(entries)
        return make_issue(created_days_ago=100).with_status_since(changes[-1].changed_at)

    def test_reopening_resets_the_clock_so_the_issue_is_not_long_running(
        self, check: StatusAgeCheck
    ) -> None:
        """The age is 10 days, not 90 — so at a 14-day threshold nothing is flagged."""
        assert check.evaluate([self._reopened_issue()], _ctx(14)) == []

    def test_the_age_measured_is_the_latest_transition_not_the_first(
        self, check: StatusAgeCheck
    ) -> None:
        """Tighten the threshold to 7 and the reported age must be 10, never 90 or 100."""
        finding = check.evaluate([self._reopened_issue()], _ctx(7))[0]
        assert finding.measured_value == 10

    def test_history_arriving_newest_first_still_yields_the_latest_change(
        self, check: StatusAgeCheck
    ) -> None:
        """The pagination trap: never trust the order Jira returns changelog pages in."""
        entries = [
            {
                "created": (FROZEN_NOW - timedelta(days=days)).isoformat(),
                "items": [{"field": "status", "fromString": "A", "toString": "B"}],
            }
            for days in [32, 46, 40, 34, 44, 36, 42, 38]
        ]
        changes = normalize_status_changes(entries)
        issue = make_issue(created_days_ago=50).with_status_since(changes[-1].changed_at)
        assert check.evaluate([issue], _ctx(14))[0].measured_value == 32


class TestTimezoneHandling:
    def test_age_is_counted_in_whole_days_across_a_dst_boundary(
        self, check: StatusAgeCheck
    ) -> None:
        """US DST ended 2026-11-01; 20 calendar days must still read as 20."""
        now = datetime(2026, 11, 5, 9, 0, tzinfo=CHICAGO)
        ctx = ScanContext(
            now=now,
            config=ScanConfig(project_key="D", timezone="America/Chicago", status_age_days=14),
        )
        issue = make_issue(now=now, created_days_ago=40, status_since_days_ago=20)
        assert check.evaluate([issue], ctx)[0].measured_value == 20


def test_check_does_not_mutate_its_input(check: StatusAgeCheck) -> None:
    issue = make_issue(status_since_days_ago=30)
    before = issue.model_dump()
    check.evaluate([issue], _ctx(14))
    assert issue.model_dump() == before
