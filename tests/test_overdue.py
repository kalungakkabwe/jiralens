"""Check 2 — overdue unresolved work.

The boundary that matters: an issue due *today* is not overdue.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from jiralens.checks.overdue import OverdueCheck
from jiralens.models import ScanConfig, ScanContext
from tests.conftest import FROZEN_NOW, LUSAKA
from tests.factories import make_issue


@pytest.fixture
def check() -> OverdueCheck:
    return OverdueCheck()


class TestTheDueDateBoundary:
    def test_due_yesterday_is_overdue(self, check: OverdueCheck, ctx: ScanContext) -> None:
        findings = check.evaluate([make_issue(due_days_from_now=-1)], ctx)
        assert len(findings) == 1
        assert findings[0].measured_value == 1

    def test_due_today_is_not_overdue(self, check: OverdueCheck, ctx: ScanContext) -> None:
        assert check.evaluate([make_issue(due_days_from_now=0)], ctx) == []

    def test_due_tomorrow_is_not_overdue(self, check: OverdueCheck, ctx: ScanContext) -> None:
        assert check.evaluate([make_issue(due_days_from_now=1)], ctx) == []

    def test_no_due_date_is_never_overdue(self, check: OverdueCheck, ctx: ScanContext) -> None:
        assert check.evaluate([make_issue(due_days_from_now=None)], ctx) == []

    @pytest.mark.parametrize("days", [1, 2, 12, 31, 365])
    def test_days_overdue_is_reported_exactly(
        self, check: OverdueCheck, ctx: ScanContext, days: int
    ) -> None:
        finding = check.evaluate([make_issue(due_days_from_now=-days)], ctx)[0]
        assert finding.measured_value == days
        assert finding.threshold_value == 0


class TestDoneIssues:
    def test_done_and_overdue_is_not_flagged(self, check: OverdueCheck, ctx: ScanContext) -> None:
        """Delivered late is a retrospective matter, not an open risk."""
        issue = make_issue(status="Done", due_days_from_now=-30)
        assert check.evaluate([issue], ctx) == []

    def test_open_issue_alongside_a_done_one_is_still_flagged(
        self, check: OverdueCheck, ctx: ScanContext
    ) -> None:
        issues = [
            make_issue("DEMO-1", status="Done", due_days_from_now=-30),
            make_issue("DEMO-2", status="In Progress", due_days_from_now=-5),
        ]
        findings = check.evaluate(issues, ctx)
        assert [f.issue_key for f in findings] == ["DEMO-2"]


class TestSeverity:
    @pytest.mark.parametrize("priority", ["Highest", "High"])
    def test_high_priorities_escalate(
        self, check: OverdueCheck, ctx: ScanContext, priority: str
    ) -> None:
        finding = check.evaluate([make_issue(due_days_from_now=-3, priority=priority)], ctx)[0]
        assert finding.severity == "high"

    @pytest.mark.parametrize("priority", ["Medium", "Low", "Lowest", None])
    def test_other_priorities_are_warnings(
        self, check: OverdueCheck, ctx: ScanContext, priority: str | None
    ) -> None:
        finding = check.evaluate([make_issue(due_days_from_now=-3, priority=priority)], ctx)[0]
        assert finding.severity == "warning"

    def test_priority_matching_is_case_insensitive(
        self, check: OverdueCheck, ctx: ScanContext
    ) -> None:
        finding = check.evaluate([make_issue(due_days_from_now=-3, priority="HIGH")], ctx)[0]
        assert finding.severity == "high"

    def test_configured_custom_priority_names_escalate(self, check: OverdueCheck) -> None:
        ctx = ScanContext(
            now=FROZEN_NOW,
            config=ScanConfig(project_key="DEMO", high_priorities=frozenset({"P1", "Blocker"})),
        )
        assert check.evaluate([make_issue(due_days_from_now=-3, priority="P1")], ctx)[
            0
        ].severity == ("high")
        assert (
            check.evaluate([make_issue(due_days_from_now=-3, priority="Highest")], ctx)[0].severity
            == "warning"
        )


class TestTimezoneSensitivity:
    """`duedate` is a bare calendar date, so "today" must come from the display zone."""

    def test_the_same_instant_gives_different_answers_in_different_zones(
        self, check: OverdueCheck
    ) -> None:
        # 01:30 on the 5th in Lusaka is still the 4th in Chicago.
        moment = datetime(2026, 11, 5, 1, 30, tzinfo=LUSAKA)
        issue = make_issue(due_days_from_now=None).model_copy(
            update={"due_date": datetime(2026, 11, 4, tzinfo=LUSAKA).date()}
        )

        lusaka = ScanContext(
            now=moment, config=ScanConfig(project_key="D", timezone="Africa/Lusaka")
        )
        chicago = ScanContext(
            now=moment, config=ScanConfig(project_key="D", timezone="America/Chicago")
        )

        assert len(check.evaluate([issue], lusaka)) == 1  # the 4th has passed
        assert check.evaluate([issue], chicago) == []  # it is still the 4th


class TestFindingContent:
    def test_reason_states_the_day_count_and_the_date(
        self, check: OverdueCheck, ctx: ScanContext
    ) -> None:
        finding = check.evaluate([make_issue(due_days_from_now=-12)], ctx)[0]
        assert "12 calendar days" in finding.reason
        assert "not complete" in finding.reason

    def test_one_day_overdue_is_not_pluralised(self, check: OverdueCheck, ctx: ScanContext) -> None:
        finding = check.evaluate([make_issue(due_days_from_now=-1)], ctx)[0]
        assert "1 calendar day " in finding.reason
        assert "1 calendar days" not in finding.reason


def test_check_does_not_mutate_its_input(check: OverdueCheck, ctx: ScanContext) -> None:
    issue = make_issue(due_days_from_now=-5)
    before = issue.model_dump()
    check.evaluate([issue], ctx)
    assert issue.model_dump() == before
