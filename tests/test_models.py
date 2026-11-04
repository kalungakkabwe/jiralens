"""Domain model invariants, including the DST boundary the injected clock makes testable."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from jiralens.models import (
    Finding,
    IssueRecord,
    ScanConfig,
    ScanContext,
    ScanResult,
    StatusChange,
)
from tests.conftest import CHICAGO, FROZEN_NOW, LUSAKA


def _issue(key: str = "DEMO-1", **kw: object) -> IssueRecord:
    defaults: dict[str, object] = {
        "id": "10001",
        "key": key,
        "url": f"https://x.example.net/browse/{key}",
        "summary": "Something",
        "issue_type": "Task",
        "status": "In Progress",
        "status_category": "indeterminate",
        "created_at": FROZEN_NOW - timedelta(days=30),
        "updated_at": FROZEN_NOW - timedelta(days=1),
    }
    return IssueRecord(**{**defaults, **kw})  # type: ignore[arg-type]


def _finding(severity: str = "high", key: str = "DEMO-1", measured: float | None = 1) -> Finding:
    return Finding(
        check_id="overdue",
        check_name="Overdue unresolved work",
        severity=severity,  # type: ignore[arg-type]
        issue_key=key,
        issue_url=f"https://x.example.net/browse/{key}",
        summary="Something",
        reason="because",
        recommendation="do something",
        measured_value=measured,
        threshold_value=0,
    )


class TestAwarenessIsEnforced:
    def test_naive_created_at_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _issue(created_at=datetime(2026, 1, 1))  # noqa: DTZ001

    def test_naive_now_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ScanContext(now=datetime(2026, 1, 1), config=ScanConfig(project_key="DEMO"))  # noqa: DTZ001

    def test_naive_status_change_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            StatusChange(changed_at=datetime(2026, 1, 1))  # noqa: DTZ001

    def test_optional_timestamps_accept_none(self) -> None:
        assert _issue(resolution_date=None).resolution_date is None


class TestScanConfig:
    def test_project_key_is_upper_cased(self) -> None:
        assert ScanConfig(project_key="its").project_key == "ITS"

    def test_whitespace_is_stripped(self) -> None:
        assert ScanConfig(project_key="  its  ").project_key == "ITS"

    def test_empty_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScanConfig(project_key="   ")

    def test_key_with_punctuation_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a valid Jira project key"):
            ScanConfig(project_key="ITS-1")

    @pytest.mark.parametrize("field", ["stale_days", "high_priority_stale_days", "status_age_days"])
    def test_thresholds_must_be_positive(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ScanConfig(project_key="DEMO", **{field: 0})  # type: ignore[arg-type]

    def test_default_jql_is_bounded_and_ordered(self) -> None:
        jql = ScanConfig(project_key="ITS").default_jql()
        assert jql == "project = ITS AND statusCategory != Done ORDER BY updated ASC"

    def test_custom_jql_wins(self) -> None:
        cfg = ScanConfig(project_key="ITS", jql="project = ITS AND created >= -365d")
        assert cfg.effective_jql() == "project = ITS AND created >= -365d"

    def test_priority_matching_is_case_insensitive(self) -> None:
        cfg = ScanConfig(project_key="DEMO", high_priorities=frozenset({"Highest", "High"}))
        assert "highest" in cfg.high_priorities_folded
        assert "HIGH".casefold() in cfg.high_priorities_folded

    def test_custom_priority_names_are_supported(self) -> None:
        cfg = ScanConfig(project_key="DEMO", high_priorities=frozenset({"P1", "Blocker"}))
        assert cfg.high_priorities_folded == frozenset({"p1", "blocker"})

    def test_config_is_immutable(self) -> None:
        with pytest.raises(ValidationError):
            ScanConfig(project_key="DEMO").stale_days = 99  # type: ignore[misc]


class TestScanContext:
    def test_local_today_uses_the_display_timezone(self) -> None:
        """01:30 on the 5th in Lusaka (UTC+2) is still the 4th in Chicago (UTC-6).

        This is the off-by-one that makes the overdue check wrong if "today" is taken
        from the server clock instead of the configured display timezone.
        """
        moment = datetime(2026, 11, 5, 1, 30, tzinfo=LUSAKA)
        lusaka = ScanContext(
            now=moment, config=ScanConfig(project_key="D", timezone="Africa/Lusaka")
        )
        chicago = ScanContext(
            now=moment, config=ScanConfig(project_key="D", timezone="America/Chicago")
        )
        assert lusaka.local_today == date(2026, 11, 5)
        assert chicago.local_today == date(2026, 11, 4)

    def test_calendar_days_since_counts_whole_days(self, ctx: ScanContext) -> None:
        assert ctx.calendar_days_since(FROZEN_NOW - timedelta(days=46)) == 46

    def test_calendar_days_is_zero_for_today(self, ctx: ScanContext) -> None:
        assert ctx.calendar_days_since(FROZEN_NOW - timedelta(hours=2)) == 0

    def test_calendar_days_across_a_dst_boundary(self) -> None:
        """US DST ended 2026-11-01. 10 calendar days must still be 10, not 10.04."""
        cfg = ScanConfig(project_key="D", timezone="America/Chicago")
        now = datetime(2026, 11, 5, 9, 0, tzinfo=CHICAGO)
        ctx = ScanContext(now=now, config=cfg)
        earlier = datetime(2026, 10, 26, 9, 0, tzinfo=CHICAGO)
        assert ctx.calendar_days_since(earlier) == 10


class TestIssueRecord:
    @pytest.mark.parametrize(
        ("category", "expected"),
        [("new", True), ("indeterminate", True), ("done", False)],
    )
    def test_is_open_follows_the_category_key(self, category: str, expected: bool) -> None:
        assert _issue(status_category=category).is_open is expected

    def test_invalid_category_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _issue(status_category="Done")  # the display name, not the key

    def test_assignee_display_falls_back(self) -> None:
        assert _issue(assignee_name=None).assignee_display == "Unassigned"
        assert _issue(assignee_name="K. Mwale").assignee_display == "K. Mwale"

    def test_with_status_since_does_not_mutate_the_original(self) -> None:
        original = _issue()
        updated = original.with_status_since(FROZEN_NOW)
        assert original.current_status_since is None
        assert updated.current_status_since == FROZEN_NOW


class TestFindingSorting:
    def test_high_sorts_before_warning_before_info(self) -> None:
        findings = [_finding("info"), _finding("warning"), _finding("high")]
        assert [f.severity for f in sorted(findings, key=lambda f: f.sort_key)] == [
            "high",
            "warning",
            "info",
        ]

    def test_larger_measured_value_sorts_first_within_a_severity(self) -> None:
        findings = [_finding(measured=5), _finding(measured=40), _finding(measured=12)]
        assert [f.measured_value for f in sorted(findings, key=lambda f: f.sort_key)] == [40, 12, 5]

    def test_missing_measured_value_does_not_crash_sorting(self) -> None:
        findings = [_finding(measured=None), _finding(measured=3)]
        assert sorted(findings, key=lambda f: f.sort_key)[0].measured_value == 3


class TestScanResult:
    def _result(self, **kw: object) -> ScanResult:
        defaults: dict[str, object] = {
            "scan_id": "20261104-DEMO-001",
            "started_at": FROZEN_NOW,
            "completed_at": FROZEN_NOW,
            "config": ScanConfig(project_key="DEMO"),
        }
        return ScanResult(**{**defaults, **kw})  # type: ignore[arg-type]

    def test_counts_by_severity(self) -> None:
        result = self._result(
            findings=[_finding("high"), _finding("high", "DEMO-2"), _finding("warning", "DEMO-3")]
        )
        assert result.counts == {"high": 2, "warning": 1, "info": 0}
        assert result.finding_count == 3

    def test_clean_issue_count_excludes_every_flagged_issue_once(self) -> None:
        """An issue with three findings still counts as one flagged issue."""
        result = self._result(
            issues=[_issue("DEMO-1"), _issue("DEMO-2"), _issue("DEMO-3")],
            findings=[
                _finding("high", "DEMO-1"),
                _finding("warning", "DEMO-1"),
                _finding("info", "DEMO-1"),
                _finding("warning", "DEMO-2"),
            ],
        )
        assert result.issue_count == 3
        assert result.finding_count == 4
        assert result.flagged_issue_keys == {"DEMO-1", "DEMO-2"}
        assert result.clean_issue_count == 1

    def test_skipped_checks_defaults_to_empty_and_is_recorded(self) -> None:
        assert self._result().skipped_checks == []
        assert self._result(skipped_checks=["status_age"]).skipped_checks == ["status_age"]

    def test_sorted_findings_is_stable_on_issue_key(self) -> None:
        result = self._result(
            findings=[_finding("high", "DEMO-9", 5), _finding("high", "DEMO-2", 5)]
        )
        assert [f.issue_key for f in result.sorted_findings()] == ["DEMO-2", "DEMO-9"]
