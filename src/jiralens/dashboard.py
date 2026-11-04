"""Data shaping for the dashboard.

This module lives in the package rather than in ``streamlit_app.py`` for one reason:
it is testable without starting Streamlit. The app imports these functions and draws
their output, so the architectural rule holds — no audit logic, and no filtering
logic, inside the UI.

Nothing here re-derives a finding. Everything is built from a finished ``ScanResult``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from jiralens.models import ScanContext, ScanResult
from jiralens.reports.rows import build_rows
from jiralens.scanner import findings_by_check

#: Column labels for the findings table. Display names, not the CSV contract.
FINDING_COLUMNS: tuple[str, ...] = (
    "Severity",
    "Check",
    "Issue",
    "Summary",
    "Assignee",
    "Priority",
    "Status",
    "Age (days)",
    "Threshold",
    "Reason",
    "Recommendation",
    "URL",
)

ISSUE_COLUMNS: tuple[str, ...] = (
    "Issue",
    "Summary",
    "Type",
    "Status",
    "Category",
    "Assignee",
    "Priority",
    "Created",
    "Updated",
    "Due",
    "Days idle",
    "Days in status",
    "Findings",
    "URL",
)

SEVERITY_RANK = {"high": 0, "warning": 1, "info": 2}


def findings_dataframe(result: ScanResult) -> pd.DataFrame:
    """One row per finding, with issue context, ready to display."""
    rows = build_rows(result, escape=False)
    frame = pd.DataFrame(
        [
            {
                "Severity": r["severity"],
                "Check": r["check_name"],
                "Issue": r["issue_key"],
                "Summary": r["summary"],
                "Assignee": r["assignee"],
                "Priority": r["priority"] or "—",
                "Status": r["status"],
                "Age (days)": r["age_days"],
                "Threshold": r["threshold_days"],
                "Reason": r["reason"],
                "Recommendation": r["recommendation"],
                "URL": r["issue_url"],
            }
            for r in rows
        ],
        columns=list(FINDING_COLUMNS),
    )
    return frame


def issues_dataframe(result: ScanResult) -> pd.DataFrame:
    """Every scanned issue, including the ones with no findings.

    The clean issues are the point of this table: it is how an administrator confirms
    the scan actually saw the whole project rather than silently returning a subset.
    """
    ctx = ScanContext(now=result.started_at, config=result.config)
    tz = ZoneInfo(result.config.timezone)
    per_issue: dict[str, int] = {}
    for finding in result.findings:
        per_issue[finding.issue_key] = per_issue.get(finding.issue_key, 0) + 1

    def local(moment: datetime | None) -> str:
        return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M") if moment else ""

    frame = pd.DataFrame(
        [
            {
                "Issue": issue.key,
                "Summary": issue.summary,
                "Type": issue.issue_type,
                "Status": issue.status,
                "Category": issue.status_category,
                "Assignee": issue.assignee_display,
                "Priority": issue.priority or "—",
                "Created": local(issue.created_at),
                "Updated": local(issue.updated_at),
                "Due": issue.due_date.isoformat() if issue.due_date else "",
                "Days idle": ctx.calendar_days_since(issue.updated_at),
                "Days in status": ctx.calendar_days_since(
                    issue.current_status_since or issue.created_at
                ),
                "Findings": per_issue.get(issue.key, 0),
                "URL": issue.url,
            }
            for issue in result.issues
        ],
        columns=list(ISSUE_COLUMNS),
    )
    return frame


@dataclass(frozen=True)
class FilterSpec:
    """What the user has narrowed the findings table to.

    An empty collection means "no filter on this field" rather than "match nothing",
    which is what a user expects from an untouched multiselect.
    """

    severities: Sequence[str] = field(default_factory=tuple)
    checks: Sequence[str] = field(default_factory=tuple)
    assignees: Sequence[str] = field(default_factory=tuple)
    priorities: Sequence[str] = field(default_factory=tuple)
    statuses: Sequence[str] = field(default_factory=tuple)
    min_age_days: int = 0
    search: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.severities,
                self.checks,
                self.assignees,
                self.priorities,
                self.statuses,
                self.min_age_days,
                self.search.strip(),
            )
        )


def apply_filters(frame: pd.DataFrame, spec: FilterSpec) -> pd.DataFrame:
    """Narrow the findings table. Returns a new frame; never mutates the input.

    ``min_age_days`` above zero drops findings with no age at all — the unassigned
    check measures nothing, so filtering by age is a deliberate statement that the
    user wants only age-based findings.
    """
    out = frame
    for column, chosen in (
        ("Severity", spec.severities),
        ("Check", spec.checks),
        ("Assignee", spec.assignees),
        ("Priority", spec.priorities),
        ("Status", spec.statuses),
    ):
        if chosen:
            out = out[out[column].isin(list(chosen))]

    if spec.min_age_days:
        ages = pd.to_numeric(out["Age (days)"], errors="coerce")
        out = out[ages.notna() & (ages >= spec.min_age_days)]

    needle = spec.search.strip()
    if needle:
        haystack = (
            out["Issue"].astype(str)
            + " "
            + out["Summary"].astype(str)
            + " "
            + out["Reason"].astype(str)
        )
        out = out[haystack.str.contains(needle, case=False, regex=False, na=False)]

    return out.reset_index(drop=True)


def filter_options(frame: pd.DataFrame) -> dict[str, list[str]]:
    """The distinct values worth offering, per filterable column.

    Severity is ordered worst-first; the rest alphabetically, because that is how a
    user scans a long multiselect for a name they already have in mind.
    """

    def distinct(column: str) -> list[str]:
        if column not in frame.columns:
            return []
        return sorted({str(v) for v in frame[column].dropna() if str(v)})

    severities = [s for s in ("high", "warning", "info") if s in set(distinct("Severity"))]
    return {
        "Severity": severities,
        "Check": distinct("Check"),
        "Assignee": distinct("Assignee"),
        "Priority": distinct("Priority"),
        "Status": distinct("Status"),
    }


def check_counts_frame(result: ScanResult) -> pd.DataFrame:
    """Finding counts per check, shaped for a bar chart. Empty when there are none."""
    counts = findings_by_check(result)
    return pd.DataFrame(
        {"Check": list(counts), "Findings": [counts[k] for k in counts]},
        columns=["Check", "Findings"],
    )


def max_age(frame: pd.DataFrame) -> int:
    """The largest age present, for sizing an age slider. Zero when none have one."""
    if frame.empty:
        return 0
    ages = pd.to_numeric(frame["Age (days)"], errors="coerce").dropna()
    return int(ages.max()) if not ages.empty else 0


__all__ = [
    "FINDING_COLUMNS",
    "ISSUE_COLUMNS",
    "SEVERITY_RANK",
    "FilterSpec",
    "apply_filters",
    "check_counts_frame",
    "filter_options",
    "findings_dataframe",
    "issues_dataframe",
    "max_age",
]
