"""Dashboard data shaping and filtering.

These run without Streamlit, which is the point of keeping them out of the app file.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jiralens.dashboard import (
    FINDING_COLUMNS,
    ISSUE_COLUMNS,
    FilterSpec,
    apply_filters,
    filter_options,
    findings_dataframe,
    issues_dataframe,
    max_age,
)
from jiralens.models import ScanConfig, ScanResult
from jiralens.scanner import run_scan
from jiralens.sources.demo import DemoIssueSource
from tests.conftest import FROZEN_NOW
from tests.factories import make_issue
from tests.test_scanner import FakeSource


@pytest.fixture(scope="module")
def result() -> ScanResult:
    config = ScanConfig(project_key="DEMO", excluded_statuses=frozenset({"To Do"}))
    return run_scan(DemoIssueSource(now=FROZEN_NOW), config, now=FROZEN_NOW)


@pytest.fixture(scope="module")
def findings(result: ScanResult) -> pd.DataFrame:
    return findings_dataframe(result)


@pytest.fixture(scope="module")
def issues(result: ScanResult) -> pd.DataFrame:
    return issues_dataframe(result)


class TestFindingsFrame:
    def test_columns_are_the_declared_set(self, findings: pd.DataFrame) -> None:
        assert tuple(findings.columns) == FINDING_COLUMNS

    def test_one_row_per_finding(self, findings: pd.DataFrame, result: ScanResult) -> None:
        assert len(findings) == result.finding_count

    def test_severity_order_is_preserved_from_the_scan(self, findings: pd.DataFrame) -> None:
        rank = {"high": 0, "warning": 1, "info": 2}
        order = [rank[s] for s in findings["Severity"]]
        assert order == sorted(order)

    def test_missing_priority_renders_as_a_dash(self, findings: pd.DataFrame) -> None:
        assert "" not in set(findings["Priority"])

    def test_urls_are_present_for_linking(self, findings: pd.DataFrame) -> None:
        assert all("/browse/" in url for url in findings["URL"])

    def test_an_empty_scan_yields_an_empty_frame_with_columns(self) -> None:
        empty = run_scan(FakeSource([]), ScanConfig(project_key="E"), now=FROZEN_NOW)
        frame = findings_dataframe(empty)
        assert frame.empty
        assert tuple(frame.columns) == FINDING_COLUMNS


class TestIssuesFrame:
    def test_columns_are_the_declared_set(self, issues: pd.DataFrame) -> None:
        assert tuple(issues.columns) == ISSUE_COLUMNS

    def test_every_scanned_issue_appears(self, issues: pd.DataFrame, result: ScanResult) -> None:
        assert len(issues) == result.issue_count

    def test_clean_issues_are_included_with_a_zero_count(
        self, issues: pd.DataFrame, result: ScanResult
    ) -> None:
        """The clean rows are how an administrator verifies scan completeness."""
        clean = issues[issues["Findings"] == 0]
        assert len(clean) == result.clean_issue_count
        assert len(clean) > 0

    def test_finding_counts_per_issue_reconcile_with_the_scan(
        self, issues: pd.DataFrame, result: ScanResult
    ) -> None:
        assert int(issues["Findings"].sum()) == result.finding_count

    def test_days_idle_and_days_in_status_are_whole_numbers(self, issues: pd.DataFrame) -> None:
        assert all(isinstance(v, int | float) for v in issues["Days idle"])
        assert (issues["Days idle"] >= 0).all()
        assert (issues["Days in status"] >= 0).all()

    def test_an_issue_that_never_transitioned_falls_back_to_creation(self) -> None:
        issue = make_issue("DEMO-1", created_days_ago=40, status_since_days_ago=None)
        res = run_scan(FakeSource([issue]), ScanConfig(project_key="DEMO"), now=FROZEN_NOW)
        frame = issues_dataframe(res)
        assert int(frame.loc[0, "Days in status"]) == 40

    def test_unassigned_issues_are_labelled(self, issues: pd.DataFrame) -> None:
        assert "Unassigned" in set(issues["Assignee"])


class TestFilterOptions:
    def test_severity_is_ordered_worst_first(self, findings: pd.DataFrame) -> None:
        options = filter_options(findings)["Severity"]
        assert options == [s for s in ("high", "warning", "info") if s in options]

    def test_other_options_are_alphabetical(self, findings: pd.DataFrame) -> None:
        checks = filter_options(findings)["Check"]
        assert checks == sorted(checks)

    def test_options_contain_no_blanks(self, findings: pd.DataFrame) -> None:
        for values in filter_options(findings).values():
            assert "" not in values

    def test_an_empty_frame_yields_empty_options(self) -> None:
        empty = findings_dataframe(
            run_scan(FakeSource([]), ScanConfig(project_key="E"), now=FROZEN_NOW)
        )
        assert all(v == [] for v in filter_options(empty).values())


class TestFilters:
    def test_an_empty_spec_changes_nothing(self, findings: pd.DataFrame) -> None:
        assert len(apply_filters(findings, FilterSpec())) == len(findings)

    def test_an_empty_spec_reports_itself_as_empty(self) -> None:
        assert FilterSpec().is_empty
        assert not FilterSpec(severities=["high"]).is_empty
        assert not FilterSpec(min_age_days=5).is_empty
        assert not FilterSpec(search="vpn").is_empty

    def test_severity_filter(self, findings: pd.DataFrame) -> None:
        out = apply_filters(findings, FilterSpec(severities=["high"]))
        assert set(out["Severity"]) == {"high"}
        assert 0 < len(out) < len(findings)

    def test_check_filter(self, findings: pd.DataFrame) -> None:
        name = findings["Check"].iloc[0]
        out = apply_filters(findings, FilterSpec(checks=[name]))
        assert set(out["Check"]) == {name}

    def test_assignee_filter(self, findings: pd.DataFrame) -> None:
        out = apply_filters(findings, FilterSpec(assignees=["Unassigned"]))
        assert set(out["Assignee"]) == {"Unassigned"}

    def test_priority_filter(self, findings: pd.DataFrame) -> None:
        out = apply_filters(findings, FilterSpec(priorities=["Highest"]))
        assert set(out["Priority"]) == {"Highest"}

    def test_status_filter(self, findings: pd.DataFrame) -> None:
        status = findings["Status"].iloc[0]
        out = apply_filters(findings, FilterSpec(statuses=[status]))
        assert set(out["Status"]) == {status}

    def test_filters_combine_as_and(self, findings: pd.DataFrame) -> None:
        out = apply_filters(findings, FilterSpec(severities=["high"], assignees=["Unassigned"]))
        assert set(out["Severity"]) <= {"high"}
        assert set(out["Assignee"]) <= {"Unassigned"}

    def test_a_filter_matching_nothing_returns_an_empty_frame_not_an_error(
        self, findings: pd.DataFrame
    ) -> None:
        out = apply_filters(findings, FilterSpec(assignees=["Nobody At All"]))
        assert out.empty
        assert tuple(out.columns) == FINDING_COLUMNS

    def test_minimum_age_filter(self, findings: pd.DataFrame) -> None:
        out = apply_filters(findings, FilterSpec(min_age_days=40))
        ages = pd.to_numeric(out["Age (days)"])
        assert (ages >= 40).all()

    def test_minimum_age_drops_findings_that_measure_nothing(self, findings: pd.DataFrame) -> None:
        """The unassigned check has no age, so filtering by age excludes it."""
        assert findings["Age (days)"].isna().any(), "fixture has no age-less finding"
        out = apply_filters(findings, FilterSpec(min_age_days=1))
        assert not out["Age (days)"].isna().any()

    def test_zero_minimum_age_keeps_age_less_findings(self, findings: pd.DataFrame) -> None:
        assert len(apply_filters(findings, FilterSpec(min_age_days=0))) == len(findings)

    def test_search_matches_an_issue_key(self, findings: pd.DataFrame) -> None:
        key = findings["Issue"].iloc[0]
        out = apply_filters(findings, FilterSpec(search=key))
        assert set(out["Issue"]) == {key}

    def test_search_is_case_insensitive(self, findings: pd.DataFrame) -> None:
        key = findings["Issue"].iloc[0]
        assert len(apply_filters(findings, FilterSpec(search=key.lower()))) >= 1

    def test_search_treats_the_needle_as_literal_text(self, findings: pd.DataFrame) -> None:
        """A summary containing regex characters must not blow up the filter."""
        out = apply_filters(findings, FilterSpec(search="=cmd|"))
        assert len(out) >= 1

    def test_search_whitespace_only_is_ignored(self, findings: pd.DataFrame) -> None:
        assert len(apply_filters(findings, FilterSpec(search="   "))) == len(findings)

    def test_filtering_never_mutates_the_source_frame(self, findings: pd.DataFrame) -> None:
        before = len(findings)
        apply_filters(findings, FilterSpec(severities=["high"]))
        assert len(findings) == before

    def test_the_index_is_reset_so_display_is_contiguous(self, findings: pd.DataFrame) -> None:
        out = apply_filters(findings, FilterSpec(severities=["warning"]))
        assert list(out.index) == list(range(len(out)))


class TestMaxAge:
    def test_it_returns_the_largest_age(self, findings: pd.DataFrame) -> None:
        ages = pd.to_numeric(findings["Age (days)"], errors="coerce").dropna()
        assert max_age(findings) == int(ages.max())

    def test_an_empty_frame_has_no_age(self) -> None:
        empty = findings_dataframe(
            run_scan(FakeSource([]), ScanConfig(project_key="E"), now=FROZEN_NOW)
        )
        assert max_age(empty) == 0

    def test_a_frame_with_only_age_less_findings_has_no_age(self) -> None:
        """A brand-new unassigned issue breaches only the one check that has no age."""
        issue = make_issue(assignee=None, created_days_ago=3, updated_days_ago=1)
        res = run_scan(FakeSource([issue]), ScanConfig(project_key="DEMO"), now=FROZEN_NOW)
        frame = findings_dataframe(res)
        assert list(frame["Check"]) == ["Unassigned open work"]
        assert max_age(frame) == 0
