"""Issue sources.

``run_scan`` depends on this protocol, not on ``JiraClient``. That is what lets demo
mode run the real engine instead of a parallel fake, and it lets the whole audit
pipeline be tested with no HTTP mocking at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from jiralens.models import IssueRecord, ScanConfig, StatusChange


@runtime_checkable
class IssueSource(Protocol):
    """Anything that can supply issues and status histories for a scan."""

    #: Short label recorded on the ScanResult, e.g. "demo" or "jira".
    name: str

    def fetch_issues(self, config: ScanConfig) -> list[IssueRecord]:
        """Return every issue matching the config's effective JQL."""
        ...

    def fetch_status_history(self, issue_keys: Sequence[str]) -> dict[str, list[StatusChange]]:
        """Return status transitions per issue key, oldest first.

        Keys with no recorded transitions may be absent from the mapping; callers
        fall back to the issue's creation timestamp.
        """
        ...


__all__ = ["IssueSource"]
