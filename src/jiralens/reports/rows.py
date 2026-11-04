"""The flat finding row.

Both the CSV export and the HTML report need findings joined back to their issues —
a ``Finding`` carries the issue key but not its assignee, status or dates. Building
that join once, here, keeps the two exporters from drifting apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from zoneinfo import ZoneInfo

from jiralens.models import IssueRecord, ScanResult

#: CSV column order. This is a published contract — downstream spreadsheets and
#: scripts depend on it, so append new columns at the end and never rename one.
CSV_COLUMNS: tuple[str, ...] = (
    "scan_id",
    "scan_time",
    "project_key",
    "issue_key",
    "issue_url",
    "summary",
    "check_id",
    "check_name",
    "severity",
    "reason",
    "assignee",
    "priority",
    "status",
    "status_category",
    "created",
    "updated",
    "due_date",
    "age_days",
    "threshold_days",
    "recommendation",
)

#: Leading characters a spreadsheet treats as the start of a formula. A Jira summary
#: beginning with one of these would execute on open, so every text cell is prefixed
#: with an apostrophe. Tab and carriage return are included because they can be used
#: to smuggle a formula past a naive first-character check.
FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def escape_formula(value: Any) -> Any:
    """Neutralise a spreadsheet formula in a text cell. Non-strings pass through.

    Numbers are left alone deliberately: escaping them would turn a negative value
    into text and break every downstream calculation.
    """
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value


def _local_iso(moment: Any, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).isoformat() if moment is not None else ""


def build_rows(result: ScanResult, *, escape: bool = True) -> list[dict[str, Any]]:
    """One row per finding, in report order, with issue context joined in.

    Args:
        result: the completed scan.
        escape: prefix-escape formula-looking text cells. True for CSV, where a
            spreadsheet will interpret them; False for HTML, where Jinja2 escaping
            already handles safety and an apostrophe would just be visible noise.
    """
    tz = ZoneInfo(result.config.timezone)
    issues: dict[str, IssueRecord] = {i.key: i for i in result.issues}
    scan_time = _local_iso(result.started_at, tz)

    rows: list[dict[str, Any]] = []
    for finding in result.findings:
        issue = issues.get(finding.issue_key)
        row: dict[str, Any] = {
            "scan_id": result.scan_id,
            "scan_time": scan_time,
            "project_key": result.config.project_key,
            "issue_key": finding.issue_key,
            "issue_url": finding.issue_url,
            "summary": finding.summary,
            "check_id": finding.check_id,
            "check_name": finding.check_name,
            "severity": finding.severity,
            "reason": finding.reason,
            "assignee": issue.assignee_display if issue else "",
            "priority": (issue.priority if issue else None) or "",
            "status": issue.status if issue else "",
            "status_category": issue.status_category if issue else "",
            "created": _local_iso(issue.created_at if issue else None, tz),
            "updated": _local_iso(issue.updated_at if issue else None, tz),
            "due_date": issue.due_date.isoformat() if issue and issue.due_date else "",
            "age_days": finding.measured_value,
            "threshold_days": finding.threshold_value,
            "recommendation": finding.recommendation,
        }
        if escape:
            row = {k: escape_formula(v) for k, v in row.items()}
        rows.append(row)
    return rows


def group_by(rows: Sequence[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    """Count findings per distinct value of ``key``, split by severity.

    Returned in descending order of high findings, then total — the order an
    administrator wants to read, worst first.
    """
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = grouped.setdefault(
            str(row.get(key) or "—"), {"high": 0, "warning": 0, "info": 0, "total": 0}
        )
        bucket[row["severity"]] += 1
        bucket["total"] += 1
    return dict(sorted(grouped.items(), key=lambda kv: (-kv[1]["high"], -kv[1]["total"], kv[0])))


__all__ = [
    "CSV_COLUMNS",
    "FORMULA_TRIGGERS",
    "build_rows",
    "escape_formula",
    "group_by",
]
