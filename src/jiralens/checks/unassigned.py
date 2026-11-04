"""Check 1 — unassigned open work."""

from __future__ import annotations

from collections.abc import Sequence

from jiralens.checks.base import BaseCheck
from jiralens.models import Finding, IssueRecord, ScanContext


class UnassignedCheck(BaseCheck):
    """Open work with nobody accountable for it.

    Rule: ``status_category != "done" and assignee is None``.

    Deliberate limitations:

    * A team or group field does not count as an assignee. Jira's ``assignee`` is the
      only field that carries individual accountability.
    * Whether the assignee is still an active user is a later module, not MVP.
    * Done issues are never flagged, however they were closed.
    """

    id = "unassigned"
    name = "Unassigned open work"
    description = "Open issues with no assignee. Nobody is individually accountable for the work."
    requires_changelog = False

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        for issue in issues:
            if not issue.is_open or issue.assignee_account_id is not None:
                continue
            findings.append(
                self.finding(
                    issue,
                    severity=self.severity_for(issue, ctx),
                    reason=f"Issue is open in '{issue.status}' with no assignee.",
                    recommendation=(
                        "Assign an accountable owner, or confirm that this project's "
                        "unassigned-work policy is intentional."
                    ),
                )
            )
        return findings


__all__ = ["UnassignedCheck"]
