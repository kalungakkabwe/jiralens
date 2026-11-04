"""Issue builders for the check tests.

Ages are expressed in days relative to ``FROZEN_NOW``, which keeps each test's intent
readable: ``make_issue(updated_days_ago=31)`` says what it is testing.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from jiralens.models import IssueRecord
from tests.conftest import BASE_URL, FROZEN_NOW

CATEGORY_FOR_STATUS = {
    "To Do": "new",
    "In Progress": "indeterminate",
    "In Review": "indeterminate",
    "Blocked": "indeterminate",
    "Waiting for Customer": "indeterminate",
    "Done": "done",
}


def make_issue(
    key: str = "DEMO-1",
    *,
    status: str = "In Progress",
    status_category: str | None = None,
    assignee: str | None = "K. Mwale",
    priority: str | None = "Medium",
    summary: str = "Something that needs doing",
    issue_type: str = "Task",
    created_days_ago: int = 30,
    updated_days_ago: int = 1,
    due_days_from_now: int | None = None,
    status_since_days_ago: int | None = None,
    now: datetime = FROZEN_NOW,
) -> IssueRecord:
    """Build one issue. Every age is relative to ``now`` so tests read as intent."""
    category = status_category or CATEGORY_FOR_STATUS[status]
    return IssueRecord(
        id=key.split("-")[-1],
        key=key,
        url=f"{BASE_URL}/browse/{key}",
        summary=summary,
        issue_type=issue_type,
        status=status,
        status_category=category,  # type: ignore[arg-type]
        priority=priority,
        assignee_account_id=f"acc-{assignee}" if assignee else None,
        assignee_name=assignee,
        created_at=now - timedelta(days=created_days_ago),
        updated_at=now - timedelta(days=updated_days_ago),
        due_date=(now + timedelta(days=due_days_from_now)).date()
        if due_days_from_now is not None
        else None,
        resolution="Done" if category == "done" else None,
        resolution_date=now - timedelta(days=updated_days_ago) if category == "done" else None,
        current_status_since=now - timedelta(days=status_since_days_ago)
        if status_since_days_ago is not None
        else None,
    )


__all__ = ["CATEGORY_FOR_STATUS", "make_issue"]
