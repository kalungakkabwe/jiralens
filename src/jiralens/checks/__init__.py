"""Audit checks and the central registry.

Checks are registered here rather than called from inside the orchestrator, so adding
a sixth check is a one-line change and requires no edit to ``run_scan``.
"""

from __future__ import annotations

from jiralens.checks.base import AuditCheck, BaseCheck
from jiralens.checks.high_priority_stale import HighPriorityStaleCheck
from jiralens.checks.overdue import OverdueCheck
from jiralens.checks.stale import StaleCheck
from jiralens.checks.status_age import StatusAgeCheck
from jiralens.checks.unassigned import UnassignedCheck


def default_checks() -> list[AuditCheck]:
    """The v0.1.0 check set, in report order.

    Returns a fresh list each call so a caller cannot mutate the shared registry.
    """
    return [
        UnassignedCheck(),
        OverdueCheck(),
        StaleCheck(),
        HighPriorityStaleCheck(),
        StatusAgeCheck(),
    ]


#: Convenience constant for callers that only read it.
DEFAULT_CHECKS: tuple[AuditCheck, ...] = tuple(default_checks())

__all__ = [
    "DEFAULT_CHECKS",
    "AuditCheck",
    "BaseCheck",
    "HighPriorityStaleCheck",
    "OverdueCheck",
    "StaleCheck",
    "StatusAgeCheck",
    "UnassignedCheck",
    "default_checks",
]
