"""Check 4 — high-priority stale work."""

from __future__ import annotations

from collections.abc import Sequence

from jiralens.checks.base import BaseCheck
from jiralens.models import Finding, IssueRecord, ScanContext


class HighPriorityStaleCheck(BaseCheck):
    """High-priority open work that has gone quiet.

    Rule: ``priority in configured_high_priorities and status_category != "done"
    and days_since(updated) > high_priority_stale_days``.

    The priority list is configuration, not a constant, because Jira sites rename
    priorities freely — ``P1``, ``Blocker``, ``Critical``, ``Sev1``. Matching is
    case-insensitive. An issue with no priority set is never flagged here; it is
    picked up by the ordinary stale check instead.

    This check overlaps the stale check by design: a high-priority issue idle for 40
    days produces two findings, one at each threshold. The dashboard counts distinct
    flagged issues separately from total findings so the overlap is visible rather
    than double-counted.
    """

    id = "high_priority_stale"
    name = "High-priority stale work"
    description = (
        "Issues at a configured high priority with no recorded activity beyond the "
        "tighter high-priority threshold."
    )
    requires_changelog = False

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        threshold = ctx.config.high_priority_stale_days
        findings: list[Finding] = []
        for issue in issues:
            if not issue.is_open or not self.is_high_priority(issue, ctx):
                continue
            days_idle = ctx.calendar_days_since(issue.updated_at)
            if days_idle <= threshold:
                continue
            findings.append(
                self.finding(
                    issue,
                    severity="high",
                    reason=(
                        f"{issue.priority}-priority issue has had no recorded Jira "
                        f"activity for {self.plural_days(days_idle)} "
                        f"(threshold {threshold})."
                    ),
                    recommendation=(
                        "Either the priority is wrong or the work is not being "
                        "progressed. Re-triage it or reassign it today."
                    ),
                    measured_value=days_idle,
                    threshold_value=threshold,
                )
            )
        return findings


__all__ = ["HighPriorityStaleCheck"]
