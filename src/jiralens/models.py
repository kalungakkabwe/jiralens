"""Typed domain models.

Two decisions here carry the whole design:

1. ``status_category`` stores Jira's category **key** (``new`` / ``indeterminate`` /
   ``done``), never the display name. Category names are localised and site
   configurable; the keys are stable.
2. The current time is never read inside a check. It arrives as ``ScanContext.now``,
   which is what makes every check a deterministic pure function.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from jiralens.config import DEFAULT_TIMEZONE

StatusCategory = Literal["new", "indeterminate", "done"]
Severity = Literal["high", "warning", "info"]

SEVERITY_ORDER: dict[str, int] = {"high": 0, "warning": 1, "info": 2}


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware; got a naive datetime")
    return value


class StatusChange(BaseModel):
    """One status transition taken from an issue's changelog."""

    model_config = ConfigDict(frozen=True)

    changed_at: datetime
    from_status: str | None = None
    to_status: str | None = None

    @field_validator("changed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "changed_at")


class ScanConfig(BaseModel):
    """Inputs for one scan. Thresholds are in calendar days."""

    model_config = ConfigDict(frozen=True)

    project_key: str
    jql: str | None = None
    stale_days: int = Field(default=30, gt=0)
    high_priority_stale_days: int = Field(default=7, gt=0)
    status_age_days: int = Field(default=14, gt=0)
    high_priorities: frozenset[str] = frozenset({"Highest", "High"})
    excluded_statuses: frozenset[str] = frozenset()
    timezone: str = DEFAULT_TIMEZONE
    include_changelog: bool = True

    @field_validator("project_key")
    @classmethod
    def _normalise_key(cls, v: str) -> str:
        key = v.strip().upper()
        if not key:
            raise ValueError("project_key must not be empty")
        if not all(c.isalnum() or c == "_" for c in key):
            raise ValueError(f"project_key {v!r} is not a valid Jira project key")
        return key

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone {v!r}") from exc
        return v

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def high_priorities_folded(self) -> frozenset[str]:
        """Case-insensitive priority names, because sites rename these freely."""
        return frozenset(p.casefold() for p in self.high_priorities)

    @property
    def excluded_statuses_folded(self) -> frozenset[str]:
        return frozenset(s.casefold() for s in self.excluded_statuses)

    def default_jql(self) -> str:
        """The bounded query used when no custom JQL is supplied."""
        return f"project = {self.project_key} AND statusCategory != Done ORDER BY updated ASC"

    def effective_jql(self) -> str:
        return self.jql if self.jql else self.default_jql()


class IssueRecord(BaseModel):
    """A Jira issue reduced to the fields JiraLens actually audits.

    Descriptions, comments, attachments and worklogs are deliberately absent.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    key: str
    url: str
    summary: str
    issue_type: str
    status: str
    status_category: StatusCategory
    priority: str | None = None
    assignee_account_id: str | None = None
    assignee_name: str | None = None
    created_at: datetime
    updated_at: datetime
    due_date: date | None = None
    resolution: str | None = None
    resolution_date: datetime | None = None
    current_status_since: datetime | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_required(cls, v: datetime) -> datetime:
        return _require_aware(v, "timestamp")

    @field_validator("resolution_date", "current_status_since")
    @classmethod
    def _aware_optional(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _require_aware(v, "timestamp")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_open(self) -> bool:
        return self.status_category != "done"

    @property
    def assignee_display(self) -> str:
        return self.assignee_name or "Unassigned"

    def with_status_since(self, moment: datetime | None) -> IssueRecord:
        """Return a copy carrying the current-status timestamp. Never mutates."""
        return self.model_copy(update={"current_status_since": moment})


class Finding(BaseModel):
    """One audit result for one issue. A single issue may produce several."""

    model_config = ConfigDict(frozen=True)

    check_id: str
    check_name: str
    severity: Severity
    issue_key: str
    issue_url: str
    summary: str
    reason: str
    recommendation: str
    measured_value: float | int | None = None
    threshold_value: float | int | None = None

    @property
    def sort_key(self) -> tuple[int, float]:
        """Severity first, then largest measured value (most overdue, oldest) first."""
        measured = float(self.measured_value) if self.measured_value is not None else 0.0
        return (SEVERITY_ORDER[self.severity], -measured)


class ScanContext(BaseModel):
    """Everything a check needs besides the issues themselves.

    ``now`` is injected rather than read, so the suite and the demo data are
    reproducible and no test depends on the wall clock.
    """

    model_config = ConfigDict(frozen=True)

    now: datetime
    config: ScanConfig

    @field_validator("now")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "now")

    @property
    def local_today(self) -> date:
        """Today in the configured display timezone. Drives the overdue comparison."""
        return self.now.astimezone(self.config.tzinfo).date()

    def calendar_days_since(self, moment: datetime) -> int:
        """Whole calendar days between ``moment`` and ``now``, in the display timezone."""
        tz = self.config.tzinfo
        return (self.now.astimezone(tz).date() - moment.astimezone(tz).date()).days


class ScanResult(BaseModel):
    """The complete output of one scan. Every renderer consumes this and nothing else."""

    scan_id: str
    started_at: datetime
    completed_at: datetime
    config: ScanConfig
    issues: list[IssueRecord] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skipped_checks: list[str] = Field(default_factory=list)
    source_name: str = "unknown"
    duration_seconds: float = Field(default=0.0, ge=0)

    @field_validator("started_at", "completed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v, "timestamp")

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def counts(self) -> dict[str, int]:
        out = {"high": 0, "warning": 0, "info": 0}
        for finding in self.findings:
            out[finding.severity] += 1
        return out

    @property
    def flagged_issue_keys(self) -> set[str]:
        return {f.issue_key for f in self.findings}

    @property
    def clean_issue_count(self) -> int:
        """Issues scanned that produced no finding. Distinct from finding_count."""
        return self.issue_count - len(self.flagged_issue_keys)

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (f.sort_key, f.issue_key))


__all__ = [
    "SEVERITY_ORDER",
    "Finding",
    "IssueRecord",
    "ScanConfig",
    "ScanContext",
    "ScanResult",
    "Severity",
    "StatusCategory",
    "StatusChange",
]
