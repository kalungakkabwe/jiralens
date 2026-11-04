"""Jira-facing code: normalisation now, the read-only client in Phase 6."""

from jiralens.jira.normalizer import (
    ISSUE_FIELDS,
    normalize_issue,
    normalize_issues,
    normalize_status_changes,
)

__all__ = [
    "ISSUE_FIELDS",
    "normalize_issue",
    "normalize_issues",
    "normalize_status_changes",
]
