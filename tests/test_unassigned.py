"""Check 1 — unassigned open work."""

from __future__ import annotations

import pytest

from jiralens.checks.unassigned import UnassignedCheck
from jiralens.models import ScanConfig, ScanContext
from tests.conftest import FROZEN_NOW
from tests.factories import make_issue


@pytest.fixture
def check() -> UnassignedCheck:
    return UnassignedCheck()


class TestRule:
    def test_open_unassigned_is_flagged(self, check: UnassignedCheck, ctx: ScanContext) -> None:
        findings = check.evaluate([make_issue(assignee=None)], ctx)
        assert len(findings) == 1
        assert findings[0].check_id == "unassigned"
        assert findings[0].issue_key == "DEMO-1"

    def test_open_assigned_is_not_flagged(self, check: UnassignedCheck, ctx: ScanContext) -> None:
        assert check.evaluate([make_issue(assignee="T. Banda")], ctx) == []

    def test_done_unassigned_is_not_flagged(self, check: UnassignedCheck, ctx: ScanContext) -> None:
        """Closing an issue settles the accountability question."""
        assert check.evaluate([make_issue(status="Done", assignee=None)], ctx) == []

    @pytest.mark.parametrize("status", ["To Do", "In Progress", "In Review", "Blocked"])
    def test_every_open_status_is_in_scope(
        self, check: UnassignedCheck, ctx: ScanContext, status: str
    ) -> None:
        assert len(check.evaluate([make_issue(status=status, assignee=None)], ctx)) == 1

    def test_empty_input_returns_no_findings(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        assert check.evaluate([], ctx) == []

    def test_each_unassigned_issue_yields_exactly_one_finding(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        issues = [make_issue(f"DEMO-{n}", assignee=None) for n in range(1, 5)]
        issues.append(make_issue("DEMO-9", assignee="S. Zulu"))
        findings = check.evaluate(issues, ctx)
        assert len(findings) == 4
        assert {f.issue_key for f in findings} == {"DEMO-1", "DEMO-2", "DEMO-3", "DEMO-4"}


class TestSeverity:
    def test_ordinary_priority_is_a_warning(self, check: UnassignedCheck, ctx: ScanContext) -> None:
        assert check.evaluate([make_issue(assignee=None, priority="Medium")], ctx)[0].severity == (
            "warning"
        )

    def test_high_priority_unassigned_work_escalates(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        assert check.evaluate([make_issue(assignee=None, priority="Highest")], ctx)[0].severity == (
            "high"
        )

    def test_missing_priority_is_a_warning_not_a_crash(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        assert check.evaluate([make_issue(assignee=None, priority=None)], ctx)[0].severity == (
            "warning"
        )


class TestEdgeCases:
    def test_account_id_present_without_a_display_name_counts_as_assigned(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        """A deactivated user still has an accountId. Active-user checks are post-MVP."""
        issue = make_issue(assignee=None).model_copy(update={"assignee_account_id": "acc-x"})
        assert check.evaluate([issue], ctx) == []

    def test_a_display_name_alone_does_not_count_as_assigned(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        issue = make_issue(assignee=None).model_copy(update={"assignee_name": "Some Team"})
        assert len(check.evaluate([issue], ctx)) == 1


class TestFindingContent:
    def test_reason_names_the_status(self, check: UnassignedCheck, ctx: ScanContext) -> None:
        finding = check.evaluate([make_issue(status="Blocked", assignee=None)], ctx)[0]
        assert "Blocked" in finding.reason
        assert "no assignee" in finding.reason

    def test_recommendation_is_actionable(self, check: UnassignedCheck, ctx: ScanContext) -> None:
        assert "Assign" in check.evaluate([make_issue(assignee=None)], ctx)[0].recommendation

    def test_issue_url_is_carried_through_for_report_links(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        finding = check.evaluate([make_issue(assignee=None)], ctx)[0]
        assert finding.issue_url.endswith("/browse/DEMO-1")

    def test_no_threshold_is_reported_for_a_rule_without_one(
        self, check: UnassignedCheck, ctx: ScanContext
    ) -> None:
        finding = check.evaluate([make_issue(assignee=None)], ctx)[0]
        assert finding.measured_value is None
        assert finding.threshold_value is None


def test_check_does_not_mutate_its_input(check: UnassignedCheck) -> None:
    ctx = ScanContext(now=FROZEN_NOW, config=ScanConfig(project_key="DEMO"))
    issue = make_issue(assignee=None)
    before = issue.model_dump()
    check.evaluate([issue], ctx)
    assert issue.model_dump() == before
