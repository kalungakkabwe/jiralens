"""Check 3 — stale open work."""

from __future__ import annotations

from collections.abc import Sequence

from jiralens.checks.base import BaseCheck
from jiralens.models import Finding, IssueRecord, ScanContext


class StaleCheck(BaseCheck):
    """Open work with no recorded Jira activity for longer than the threshold.

    Rule: ``status_category != "done" and days_since(updated) > stale_days``.

    The threshold is **exclusive**: at the default of 30 days, an issue last updated
    exactly 30 calendar days ago is not flagged; 31 days is. This matches the written
    rule ``updated < now - threshold``, and the boundary is pinned by a test so it
    cannot drift silently.

    Limitation worth stating in every report: Jira's ``updated`` field moves for
    automation runs, bulk edits and administrative changes that represent no human
    progress at all — and conversely it does *not* move when someone works on an issue
    without touching it. This is an inactivity indicator, not evidence that the work
    has stopped. Comment and worklog analysis would sharpen it, and is deliberately
    out of MVP scope because both require fetching data this tool refuses to retrieve.
    """

    id = "stale"
    name = "Stale open work"
    description = (
        "Open issues with no recorded Jira activity beyond the stale threshold. An "
        "inactivity signal, not proof that work has stopped."
    )
    requires_changelog = False

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        threshold = ctx.config.stale_days
        findings: list[Finding] = []
        for issue in issues:
            if not issue.is_open:
                continue
            days_idle = ctx.calendar_days_since(issue.updated_at)
            if days_idle <= threshold:
                continue
            findings.append(
                self.finding(
                    issue,
                    severity=self.severity_for(issue, ctx),
                    reason=(
                        f"Issue has had no recorded Jira activity for "
                        f"{self.plural_days(days_idle)} (threshold {threshold})."
                    ),
                    recommendation=(
                        "Confirm the work is still wanted. Close it, re-prioritise it, "
                        "or record what is blocking it."
                    ),
                    measured_value=days_idle,
                    threshold_value=threshold,
                )
            )
        return findings


__all__ = ["StaleCheck"]
