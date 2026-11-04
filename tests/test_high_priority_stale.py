"""Check 4 — high-priority stale work.

The configurability of priority names is the point of this check: a site using
``P1``/``Blocker`` would otherwise have its most urgent work silently skipped.
"""

from __future__ import annotations

import pytest

from jiralens.checks.high_priority_stale import HighPriorityStaleCheck
from jiralens.models import ScanConfig, ScanContext
from tests.conftest import FROZEN_NOW
from tests.factories import make_issue


@pytest.fixture
def check() -> HighPriorityStaleCheck:
    return HighPriorityStaleCheck()


def _ctx(days: int = 7, priorities: frozenset[str] | None = None) -> ScanContext:
    config = ScanConfig(
        project_key="DEMO",
        high_priority_stale_days=days,
        high_priorities=priorities if priorities is not None else frozenset({"Highest", "High"}),
    )
    return ScanContext(now=FROZEN_NOW, config=config)


class TestThresholdBoundary:
    def test_under_the_threshold_is_not_flagged(self, check: HighPriorityStaleCheck) -> None:
        issue = make_issue(priority="Highest", updated_days_ago=6)
        assert check.evaluate([issue], _ctx(7)) == []

    def test_exactly_at_the_threshold_is_not_flagged(self, check: HighPriorityStaleCheck) -> None:
        issue = make_issue(priority="Highest", updated_days_ago=7)
        assert check.evaluate([issue], _ctx(7)) == []

    def test_over_the_threshold_is_flagged(self, check: HighPriorityStaleCheck) -> None:
        issue = make_issue(priority="Highest", updated_days_ago=8)
        findings = check.evaluate([issue], _ctx(7))
        assert len(findings) == 1
        assert findings[0].measured_value == 8
        assert findings[0].threshold_value == 7


class TestPriorityMatching:
    @pytest.mark.parametrize("priority", ["Highest", "High"])
    def test_default_high_priorities_are_matched(
        self, check: HighPriorityStaleCheck, priority: str
    ) -> None:
        issue = make_issue(priority=priority, updated_days_ago=11)
        assert len(check.evaluate([issue], _ctx())) == 1

    @pytest.mark.parametrize("priority", ["Medium", "Low", "Lowest", "Trivial"])
    def test_ordinary_priorities_are_ignored(
        self, check: HighPriorityStaleCheck, priority: str
    ) -> None:
        issue = make_issue(priority=priority, updated_days_ago=99)
        assert check.evaluate([issue], _ctx()) == []

    def test_missing_priority_is_ignored_here(self, check: HighPriorityStaleCheck) -> None:
        """An unprioritised issue is the plain stale check's business, not this one."""
        issue = make_issue(priority=None, updated_days_ago=99)
        assert check.evaluate([issue], _ctx()) == []

    @pytest.mark.parametrize("priority", ["HIGHEST", "high", "HiGh"])
    def test_matching_is_case_insensitive(
        self, check: HighPriorityStaleCheck, priority: str
    ) -> None:
        issue = make_issue(priority=priority, updated_days_ago=11)
        assert len(check.evaluate([issue], _ctx())) == 1

    def test_a_site_using_p1_and_blocker_is_supported(self, check: HighPriorityStaleCheck) -> None:
        ctx = _ctx(priorities=frozenset({"P1", "Blocker"}))
        assert len(check.evaluate([make_issue(priority="P1", updated_days_ago=11)], ctx)) == 1
        assert len(check.evaluate([make_issue(priority="Blocker", updated_days_ago=11)], ctx)) == 1

    def test_renaming_the_priority_list_excludes_the_defaults(
        self, check: HighPriorityStaleCheck
    ) -> None:
        """If a site does not use "Highest", it must stop matching."""
        ctx = _ctx(priorities=frozenset({"P1"}))
        assert check.evaluate([make_issue(priority="Highest", updated_days_ago=11)], ctx) == []


class TestScope:
    def test_done_issues_are_never_flagged(self, check: HighPriorityStaleCheck) -> None:
        issue = make_issue(status="Done", priority="Highest", updated_days_ago=400)
        assert check.evaluate([issue], _ctx()) == []

    def test_severity_is_always_high(self, check: HighPriorityStaleCheck) -> None:
        issue = make_issue(priority="High", updated_days_ago=30)
        assert check.evaluate([issue], _ctx())[0].severity == "high"


class TestOverlapWithTheStaleCheck:
    def test_both_checks_fire_on_the_same_issue_by_design(
        self, check: HighPriorityStaleCheck
    ) -> None:
        """A high-priority issue idle 40 days breaches both thresholds."""
        from jiralens.checks.stale import StaleCheck

        issue = make_issue(priority="Highest", updated_days_ago=40)
        ctx = ScanContext(
            now=FROZEN_NOW,
            config=ScanConfig(project_key="DEMO", stale_days=30, high_priority_stale_days=7),
        )
        findings = check.evaluate([issue], ctx) + StaleCheck().evaluate([issue], ctx)
        assert len(findings) == 2
        assert {f.check_id for f in findings} == {"high_priority_stale", "stale"}
        assert {f.issue_key for f in findings} == {"DEMO-1"}


class TestFindingContent:
    def test_reason_names_the_priority(self, check: HighPriorityStaleCheck) -> None:
        finding = check.evaluate([make_issue(priority="Highest", updated_days_ago=11)], _ctx())[0]
        assert "Highest" in finding.reason
        assert "11 calendar days" in finding.reason

    def test_recommendation_offers_the_two_real_options(
        self, check: HighPriorityStaleCheck
    ) -> None:
        finding = check.evaluate([make_issue(priority="High", updated_days_ago=11)], _ctx())[0]
        assert "priority is wrong" in finding.recommendation


def test_check_does_not_mutate_its_input(check: HighPriorityStaleCheck) -> None:
    issue = make_issue(priority="Highest", updated_days_ago=11)
    before = issue.model_dump()
    check.evaluate([issue], _ctx())
    assert issue.model_dump() == before
