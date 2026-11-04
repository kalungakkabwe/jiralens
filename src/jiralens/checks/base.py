"""The audit check contract.

Every check is a pure function of (issues, context). It never reads the clock, never
calls Jira, and never mutates an issue. That is what makes the suite deterministic
and what lets later checks be added without touching the orchestrator.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from jiralens.models import Finding, IssueRecord, ScanContext, Severity


@runtime_checkable
class AuditCheck(Protocol):
    """What ``run_scan`` requires of a check."""

    #: Stable machine identifier. Appears in CSV/JSON exports, so never rename it.
    id: str
    #: Human label for reports and the dashboard.
    name: str
    #: One sentence for the methodology page.
    description: str
    #: True if the check needs ``current_status_since`` populated from the changelog.
    #: The orchestrator skips such checks when ``--no-changelog`` is used and records
    #: them in ``ScanResult.skipped_checks`` — a skipped check must never report zero
    #: findings as though it had looked.
    requires_changelog: bool

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        """Return findings for the issues that breach this check's rule."""
        ...


class BaseCheck:
    """Shared helpers. Subclasses set the class attributes and implement ``evaluate``."""

    id: str = ""
    name: str = ""
    description: str = ""
    requires_changelog: bool = False

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def is_high_priority(issue: IssueRecord, ctx: ScanContext) -> bool:
        """Case-insensitive match against the configured high-priority names.

        Sites rename priorities freely (``P1``, ``Blocker``, ``Critical``), so the
        comparison is configurable and case-folded rather than hardcoded.
        """
        if issue.priority is None:
            return False
        return issue.priority.casefold() in ctx.config.high_priorities_folded

    def severity_for(self, issue: IssueRecord, ctx: ScanContext) -> Severity:
        """High when the issue is high priority, otherwise warning."""
        return "high" if self.is_high_priority(issue, ctx) else "warning"

    def finding(
        self,
        issue: IssueRecord,
        *,
        severity: Severity,
        reason: str,
        recommendation: str,
        measured_value: float | int | None = None,
        threshold_value: float | int | None = None,
    ) -> Finding:
        return Finding(
            check_id=self.id,
            check_name=self.name,
            severity=severity,
            issue_key=issue.key,
            issue_url=issue.url,
            summary=issue.summary,
            reason=reason,
            recommendation=recommendation,
            measured_value=measured_value,
            threshold_value=threshold_value,
        )

    @staticmethod
    def plural_days(count: int) -> str:
        return f"{count} calendar day" + ("" if count == 1 else "s")


__all__ = ["AuditCheck", "BaseCheck"]
