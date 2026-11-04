"""Check 5 — excessive current-status age."""

from __future__ import annotations

from collections.abc import Sequence

from jiralens.checks.base import BaseCheck
from jiralens.models import Finding, IssueRecord, ScanContext


class StatusAgeCheck(BaseCheck):
    """Open work that has sat in the same status for too long.

    Rule: ``status_category != "done" and status not excluded
    and days_in_current_status > status_age_days``.

    ``current_status_since`` comes from the issue's status changelog — the timestamp of
    the **most recent** status transition — and the orchestrator populates it before
    this check runs. Where an issue has never transitioned, it falls back to the
    creation timestamp, which is why a freshly created issue is not flagged.

    Two things to know:

    * This check sets ``requires_changelog``. Under ``--no-changelog`` the orchestrator
      skips it and records it in ``ScanResult.skipped_checks``. It must never run
      against unpopulated data and report zero findings as though it had looked —
      "0 long-running issues" when no history was fetched is worse than no answer.
    * The closest JQL cross-check, ``NOT status CHANGED AFTER -14d``, is deliberately
      *not* equivalent: it also matches issues created inside the threshold that have
      never transitioned, which this check correctly ignores. That divergence set is
      the expected difference; anything else in a diff is a real bug.

    Statuses that legitimately sit still — ``Waiting for Customer``, ``On Hold``,
    ``Waiting on Vendor`` — belong in ``excluded_statuses``. Matching is
    case-insensitive.
    """

    id = "status_age"
    name = "Excessive current-status age"
    description = (
        "Open issues that have remained in their current working status beyond the "
        "threshold, measured from the latest status transition in the changelog."
    )
    requires_changelog = True

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        threshold = ctx.config.status_age_days
        excluded = ctx.config.excluded_statuses_folded
        findings: list[Finding] = []
        for issue in issues:
            if not issue.is_open or issue.status.casefold() in excluded:
                continue
            since = issue.current_status_since or issue.created_at
            days_in_status = ctx.calendar_days_since(since)
            if days_in_status <= threshold:
                continue
            never_moved = issue.current_status_since is None
            qualifier = " since it was created" if never_moved else ""
            findings.append(
                self.finding(
                    issue,
                    severity=self.severity_for(issue, ctx),
                    reason=(
                        f"Issue has remained in '{issue.status}' for "
                        f"{self.plural_days(days_in_status)}{qualifier} "
                        f"(threshold {threshold})."
                    ),
                    recommendation=(
                        "Find out what is holding it in this status. If the wait is "
                        "legitimate, add the status to the excluded list so it stops "
                        "appearing here."
                    ),
                    measured_value=days_in_status,
                    threshold_value=threshold,
                )
            )
        return findings


__all__ = ["StatusAgeCheck"]
