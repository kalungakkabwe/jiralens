"""Check 3 — stale open work.

The threshold is exclusive: at 30 days, 30 does not flag and 31 does. That choice is
pinned here so it cannot drift silently between releases.
"""

from __future__ import annotations

import pytest

from jiralens.checks.stale import StaleCheck
from jiralens.models import ScanConfig, ScanContext
from tests.conftest import FROZEN_NOW
from tests.factories import make_issue


@pytest.fixture
def check() -> StaleCheck:
    return StaleCheck()


def _ctx(stale_days: int = 30) -> ScanContext:
    return ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO", stale_days=stale_days))


class TestThresholdBoundary:
    def test_one_day_under_the_threshold_is_not_stale(self, check: StaleCheck) -> None:
        assert check.evaluate([make_issue(updated_days_ago=29)], _ctx(30)) == []

    def test_exactly_at_the_threshold_is_not_stale(self, check: StaleCheck) -> None:
        """Exclusive boundary, matching the rule `updated < now - threshold`."""
        assert check.evaluate([make_issue(updated_days_ago=30)], _ctx(30)) == []

    def test_one_day_over_the_threshold_is_stale(self, check: StaleCheck) -> None:
        findings = check.evaluate([make_issue(updated_days_ago=31)], _ctx(30))
        assert len(findings) == 1
        assert findings[0].measured_value == 31
        assert findings[0].threshold_value == 30

    def test_updated_today_is_not_stale(self, check: StaleCheck) -> None:
        assert check.evaluate([make_issue(updated_days_ago=0)], _ctx(30)) == []

    @pytest.mark.parametrize(
        ("threshold", "age", "flagged"),
        [
            (7, 7, False),
            (7, 8, True),
            (14, 14, False),
            (14, 15, True),
            (90, 89, False),
            (90, 91, True),
        ],
    )
    def test_the_boundary_holds_at_any_configured_threshold(
        self, check: StaleCheck, threshold: int, age: int, flagged: bool
    ) -> None:
        findings = check.evaluate([make_issue(updated_days_ago=age)], _ctx(threshold))
        assert bool(findings) is flagged


class TestScope:
    def test_done_issues_are_never_stale(self, check: StaleCheck) -> None:
        issue = make_issue(status="Done", updated_days_ago=400)
        assert check.evaluate([issue], _ctx(30)) == []

    @pytest.mark.parametrize("status", ["To Do", "In Progress", "In Review", "Blocked"])
    def test_every_open_status_is_in_scope(self, check: StaleCheck, status: str) -> None:
        issue = make_issue(status=status, updated_days_ago=60)
        assert len(check.evaluate([issue], _ctx(30))) == 1

    def test_a_status_that_legitimately_waits_is_still_flagged(self, check: StaleCheck) -> None:
        """Documented behaviour: excluded_statuses applies to the status-age check only."""
        issue = make_issue(status="Waiting for Customer", updated_days_ago=60)
        ctx = ScanContext(
            now=FROZEN_NOW,
            config=ScanConfig(
                project_key="DEMO",
                stale_days=30,
                excluded_statuses=frozenset({"Waiting for Customer"}),
            ),
        )
        assert len(check.evaluate([issue], ctx)) == 1

    def test_mixed_batch_flags_only_the_stale_ones(self, check: StaleCheck) -> None:
        issues = [
            make_issue("DEMO-1", updated_days_ago=5),
            make_issue("DEMO-2", updated_days_ago=46),
            make_issue("DEMO-3", updated_days_ago=31),
            make_issue("DEMO-4", status="Done", updated_days_ago=99),
        ]
        findings = check.evaluate(issues, _ctx(30))
        assert {f.issue_key for f in findings} == {"DEMO-2", "DEMO-3"}


class TestSeverity:
    def test_ordinary_priority_is_a_warning(self, check: StaleCheck) -> None:
        finding = check.evaluate([make_issue(updated_days_ago=60, priority="Low")], _ctx(30))[0]
        assert finding.severity == "warning"

    def test_high_priority_stale_work_escalates_here_too(self, check: StaleCheck) -> None:
        finding = check.evaluate([make_issue(updated_days_ago=60, priority="Highest")], _ctx(30))[0]
        assert finding.severity == "high"


class TestFindingContent:
    def test_reason_states_the_age_and_the_threshold(self, check: StaleCheck) -> None:
        finding = check.evaluate([make_issue(updated_days_ago=46)], _ctx(30))[0]
        assert "46 calendar days" in finding.reason
        assert "threshold 30" in finding.reason

    def test_reason_is_phrased_as_inactivity_not_abandonment(self, check: StaleCheck) -> None:
        """The updated field is an inactivity signal; the wording must not overclaim."""
        finding = check.evaluate([make_issue(updated_days_ago=46)], _ctx(30))[0]
        assert "no recorded Jira activity" in finding.reason

    def test_check_id_is_stable(self, check: StaleCheck) -> None:
        assert check.evaluate([make_issue(updated_days_ago=46)], _ctx(30))[0].check_id == "stale"


def test_check_does_not_mutate_its_input(check: StaleCheck) -> None:
    issue = make_issue(updated_days_ago=46)
    before = issue.model_dump()
    check.evaluate([issue], _ctx(30))
    assert issue.model_dump() == before
