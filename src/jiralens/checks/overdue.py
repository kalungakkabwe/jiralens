"""Check 2 — overdue unresolved work."""

from __future__ import annotations

from collections.abc import Sequence

from jiralens.checks.base import BaseCheck
from jiralens.models import Finding, IssueRecord, ScanContext


class OverdueCheck(BaseCheck):
    """Open work whose due date has already passed.

    Rule: ``due_date is not None and due_date < local_today and status_category != "done"``.

    An issue due **today is not overdue**. "Today" is taken from the configured display
    timezone, not the server clock: Jira's ``duedate`` is a bare calendar date with no
    offset, so comparing it against a UTC instant produces a one-day error either side
    of midnight. The JQL equivalent is ``duedate < startOfDay()``, which Jira evaluates
    in the *Jira user's* timezone — set that to match ``JIRALENS_TIMEZONE`` before
    cross-checking, or expect phantom off-by-ones at the boundary.
    """

    id = "overdue"
    name = "Overdue unresolved work"
    description = (
        "Open issues whose due date has passed. Counted in calendar days in the "
        "configured display timezone; an issue due today is not overdue."
    )
    requires_changelog = False

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        today = ctx.local_today
        findings: list[Finding] = []
        for issue in issues:
            if not issue.is_open or issue.due_date is None or issue.due_date >= today:
                continue
            days_overdue = (today - issue.due_date).days
            findings.append(
                self.finding(
                    issue,
                    severity=self.severity_for(issue, ctx),
                    reason=(
                        f"Issue is {self.plural_days(days_overdue)} past its due date "
                        f"({issue.due_date.isoformat()}) and is not complete."
                    ),
                    recommendation=(
                        "Re-forecast the due date with the owner, or escalate if the "
                        "original commitment still stands."
                    ),
                    measured_value=days_overdue,
                    threshold_value=0,
                )
            )
        return findings


__all__ = ["OverdueCheck"]
