"""Scan orchestration.

The tests that matter most here are the honesty ones: when status history is missing
or unavailable, the scan must say the check was skipped rather than report that it
found nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from jiralens.checks import default_checks
from jiralens.checks.base import BaseCheck
from jiralens.exceptions import JiraApiError
from jiralens.models import (
    Finding,
    IssueRecord,
    ScanConfig,
    ScanContext,
    ScanResult,
    StatusChange,
)
from jiralens.scanner import (
    PERMISSION_CAVEAT,
    ScanProgress,
    enrich_status_since,
    findings_by_check,
    latest_status_change,
    make_scan_id,
    run_scan,
)
from jiralens.sources import IssueSource
from jiralens.sources.demo import DemoIssueSource
from tests.conftest import FROZEN_NOW, LUSAKA
from tests.factories import make_issue

# --------------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------------


class FakeSource:
    """An IssueSource with no network and no fixtures, for precise scenarios."""

    name = "fake"

    def __init__(
        self,
        issues: Sequence[IssueRecord] = (),
        histories: dict[str, list[StatusChange]] | None = None,
        *,
        history_error: Exception | None = None,
    ) -> None:
        self._issues = list(issues)
        self._histories = histories or {}
        self._history_error = history_error
        self.history_calls: list[list[str]] = []

    def fetch_issues(self, config: ScanConfig) -> list[IssueRecord]:
        return list(self._issues)

    def fetch_status_history(self, issue_keys: Sequence[str]) -> dict[str, list[StatusChange]]:
        self.history_calls.append(list(issue_keys))
        if self._history_error is not None:
            raise self._history_error
        return {k: v for k, v in self._histories.items() if k in set(issue_keys)}


class AlwaysFires(BaseCheck):
    id = "always"
    name = "Always fires"
    description = "A test double that reports one finding for every open issue given."
    requires_changelog = False

    def evaluate(self, issues: Sequence[IssueRecord], ctx: ScanContext) -> list[Finding]:
        return [
            self.finding(i, severity="info", reason="r", recommendation="x")
            for i in issues
            if i.is_open
        ]


class NeedsHistory(AlwaysFires):
    id = "needs_history"
    name = "Needs history"
    description = "A test double that declares a dependency on the status changelog."
    requires_changelog = True


def _change(days_ago: int, to: str = "In Progress") -> StatusChange:
    return StatusChange(
        changed_at=FROZEN_NOW - timedelta(days=days_ago), from_status="To Do", to_status=to
    )


# --------------------------------------------------------------------------------


class TestScanId:
    def test_format_is_date_project_time(self) -> None:
        assert make_scan_id("ITS", FROZEN_NOW, "Africa/Lusaka") == "20261104-ITS-103000"

    def test_it_is_rendered_in_the_configured_display_timezone(self) -> None:
        """The same instant yields a different local id in a different zone."""
        lusaka = make_scan_id("ITS", FROZEN_NOW, "Africa/Lusaka")
        chicago = make_scan_id("ITS", FROZEN_NOW, "America/Chicago")
        assert lusaka != chicago
        assert chicago.startswith("20261104-ITS-02")  # 10:30 +02:00 is 02:30 -06:00

    def test_ids_sort_chronologically(self) -> None:
        early = make_scan_id("ITS", FROZEN_NOW, "Africa/Lusaka")
        later = make_scan_id("ITS", FROZEN_NOW + timedelta(hours=3), "Africa/Lusaka")
        assert early < later


class TestLatestStatusChange:
    def test_empty_history_has_no_latest(self) -> None:
        assert latest_status_change([]) is None

    def test_single_change_is_the_latest(self) -> None:
        change = _change(5)
        assert latest_status_change([change]) is change

    def test_the_newest_wins_regardless_of_position(self) -> None:
        """The pagination trap: position in the list proves nothing."""
        changes = [_change(10), _change(2), _change(40), _change(30)]
        latest = latest_status_change(changes)
        assert latest is not None
        assert latest.changed_at == FROZEN_NOW - timedelta(days=2)

    def test_it_does_not_rely_on_the_list_being_sorted(self) -> None:
        ascending = [_change(d) for d in (40, 30, 10, 2)]
        descending = list(reversed(ascending))
        first = latest_status_change(ascending)
        second = latest_status_change(descending)
        assert first is not None and second is not None
        assert first.changed_at == second.changed_at


class TestEnrichment:
    def test_issue_with_history_gets_the_latest_transition(self) -> None:
        issues = [make_issue("DEMO-1")]
        enriched = enrich_status_since(issues, {"DEMO-1": [_change(40), _change(9)]})
        assert enriched[0].current_status_since == FROZEN_NOW - timedelta(days=9)

    def test_issue_without_history_is_left_unset_for_the_fallback(self) -> None:
        enriched = enrich_status_since([make_issue("DEMO-1")], {})
        assert enriched[0].current_status_since is None

    def test_enrichment_does_not_mutate_the_originals(self) -> None:
        issues = [make_issue("DEMO-1")]
        before = issues[0].model_dump()
        enrich_status_since(issues, {"DEMO-1": [_change(9)]})
        assert issues[0].model_dump() == before

    def test_order_is_preserved(self) -> None:
        issues = [make_issue(f"DEMO-{n}") for n in (3, 1, 2)]
        assert [i.key for i in enrich_status_since(issues, {})] == ["DEMO-3", "DEMO-1", "DEMO-2"]


class TestScanMetadata:
    def test_result_carries_the_config_source_and_timing(self, config: ScanConfig) -> None:
        source = FakeSource([make_issue()])
        result = run_scan(source, config, [AlwaysFires()], now=FROZEN_NOW)
        assert isinstance(result, ScanResult)
        assert result.config == config
        assert result.source_name == "fake"
        assert result.started_at == FROZEN_NOW
        assert result.completed_at >= result.started_at
        assert result.duration_seconds >= 0

    def test_the_injected_clock_is_what_the_checks_see(self) -> None:
        """A scan at a fixed instant is reproducible, which is the whole point."""
        config = ScanConfig(project_key="DEMO", stale_days=30)
        issue = make_issue(updated_days_ago=31, now=FROZEN_NOW)
        first = run_scan(FakeSource([issue]), config, now=FROZEN_NOW)
        second = run_scan(FakeSource([issue]), config, now=FROZEN_NOW)
        assert [f.model_dump() for f in first.findings] == [f.model_dump() for f in second.findings]

    def test_default_check_set_is_used_when_none_is_given(self, config: ScanConfig) -> None:
        result = run_scan(FakeSource([make_issue(assignee=None)]), config, now=FROZEN_NOW)
        assert any(f.check_id == "unassigned" for f in result.findings)


class TestEmptyResults:
    def test_no_issues_still_produces_a_valid_result(self, config: ScanConfig) -> None:
        result = run_scan(FakeSource([]), config, now=FROZEN_NOW)
        assert result.issue_count == 0
        assert result.findings == []
        assert result.clean_issue_count == 0

    def test_no_issues_is_explained_in_the_warnings(self, config: ScanConfig) -> None:
        result = run_scan(FakeSource([]), config, now=FROZEN_NOW)
        assert any("No issues matched" in w for w in result.warnings)

    def test_history_is_not_requested_when_there_are_no_issues(self, config: ScanConfig) -> None:
        source = FakeSource([])
        run_scan(source, config, now=FROZEN_NOW)
        assert source.history_calls == []


class TestChangelogHonesty:
    """A skipped check must never look like a check that found nothing."""

    def test_no_changelog_config_skips_the_dependent_check(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        result = run_scan(
            FakeSource([make_issue()]), config, [AlwaysFires(), NeedsHistory()], now=FROZEN_NOW
        )
        assert result.skipped_checks == ["needs_history"]
        assert {f.check_id for f in result.findings} == {"always"}

    def test_no_changelog_does_not_call_the_source_for_history(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        source = FakeSource([make_issue()])
        run_scan(source, config, now=FROZEN_NOW)
        assert source.history_calls == []

    def test_no_changelog_is_explained_in_the_warnings(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        result = run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW)
        assert any("Status history was not retrieved" in w for w in result.warnings)
        assert any("skipped" in w for w in result.warnings)

    def test_a_failed_history_fetch_degrades_instead_of_aborting(self) -> None:
        """A partial audit that names what is missing beats no audit."""
        source = FakeSource([make_issue()], history_error=JiraApiError("bulkfetch returned 503"))
        result = run_scan(
            source,
            ScanConfig(project_key="DEMO"),
            [AlwaysFires(), NeedsHistory()],
            now=FROZEN_NOW,
        )
        assert result.skipped_checks == ["needs_history"]
        assert any("could not be retrieved" in w for w in result.warnings)
        assert any("503" in w for w in result.warnings)
        assert {f.check_id for f in result.findings} == {"always"}

    def test_status_age_is_the_check_skipped_in_the_real_set(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        result = run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW)
        assert result.skipped_checks == ["status_age"]

    def test_findings_by_check_omits_skipped_checks_entirely(self) -> None:
        """A zero next to a skipped check would be a lie; it must be absent."""
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        result = run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW)
        counts = findings_by_check(result)
        assert "status_age" not in counts
        assert "stale" in counts

    def test_issues_missing_history_are_counted_in_a_warning(self) -> None:
        source = FakeSource(
            [make_issue("DEMO-1"), make_issue("DEMO-2")], histories={"DEMO-1": [_change(5)]}
        )
        result = run_scan(source, ScanConfig(project_key="DEMO"), now=FROZEN_NOW)
        assert any("1 issue(s) have no recorded status transition" in w for w in result.warnings)


class TestWarnings:
    def test_the_permission_caveat_is_always_present(self, config: ScanConfig) -> None:
        result = run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW)
        assert PERMISSION_CAVEAT in result.warnings

    def test_excluded_statuses_are_disclosed(self) -> None:
        config = ScanConfig(
            project_key="DEMO", excluded_statuses=frozenset({"On Hold", "Waiting for Customer"})
        )
        result = run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW)
        disclosure = [w for w in result.warnings if "excluded from" in w]
        assert len(disclosure) == 1
        assert "On Hold" in disclosure[0]
        assert "Waiting for Customer" in disclosure[0]

    def test_no_exclusion_warning_when_nothing_is_excluded(self, config: ScanConfig) -> None:
        result = run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW)
        assert not [w for w in result.warnings if "excluded from" in w]


class TestFindingOrder:
    def test_findings_come_back_sorted_by_severity(self) -> None:
        issues = [
            make_issue("DEMO-1", priority="Low", updated_days_ago=60),
            make_issue("DEMO-2", priority="Highest", updated_days_ago=60),
        ]
        result = run_scan(FakeSource(issues), ScanConfig(project_key="DEMO"), now=FROZEN_NOW)
        severities = [f.severity for f in result.findings]
        assert severities == sorted(
            severities, key=lambda s: {"high": 0, "warning": 1, "info": 2}[s]
        )

    def test_the_most_extreme_value_leads_within_a_severity(self) -> None:
        issues = [
            make_issue("DEMO-1", priority="Low", updated_days_ago=40),
            make_issue("DEMO-2", priority="Low", updated_days_ago=120),
        ]
        result = run_scan(
            FakeSource(issues),
            ScanConfig(project_key="DEMO"),
            [default_checks()[2]],
            now=FROZEN_NOW,
        )
        assert [f.issue_key for f in result.findings] == ["DEMO-2", "DEMO-1"]

    def test_the_order_is_stable_across_runs(self) -> None:
        issues = [make_issue(f"DEMO-{n}", updated_days_ago=60, assignee=None) for n in range(1, 6)]
        config = ScanConfig(project_key="DEMO")
        first = run_scan(FakeSource(issues), config, now=FROZEN_NOW)
        second = run_scan(FakeSource(issues), config, now=FROZEN_NOW)
        assert [(f.issue_key, f.check_id) for f in first.findings] == [
            (f.issue_key, f.check_id) for f in second.findings
        ]


class TestProgress:
    def test_every_stage_is_reported_in_order(self, config: ScanConfig) -> None:
        seen: list[ScanProgress] = []
        run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW, on_progress=seen.append)
        stages = [p.stage for p in seen]
        assert stages[0] == "Retrieving issues"
        assert "Running checks" in stages
        assert stages[-1] == "Complete"

    def test_the_jql_is_surfaced_so_a_user_can_see_what_ran(self, config: ScanConfig) -> None:
        seen: list[ScanProgress] = []
        run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW, on_progress=seen.append)
        assert config.effective_jql() in seen[0].detail

    def test_history_stage_is_absent_when_the_changelog_is_skipped(self) -> None:
        seen: list[ScanProgress] = []
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW, on_progress=seen.append)
        assert not any("histories" in p.stage for p in seen)

    def test_a_scan_runs_fine_with_no_callback(self, config: ScanConfig) -> None:
        assert run_scan(FakeSource([make_issue()]), config, now=FROZEN_NOW).issue_count == 1

    def test_progress_renders_readably(self) -> None:
        assert str(ScanProgress("Running checks", "5 check(s)")) == "Running checks — 5 check(s)"
        assert str(ScanProgress("Complete")) == "Complete"


class TestAgainstTheDemoSource:
    """The end-to-end test the Phase 3 exit criteria call for."""

    @pytest.fixture
    def result(self) -> ScanResult:
        config = ScanConfig(
            project_key="DEMO", excluded_statuses=frozenset({"Waiting for Customer"})
        )
        return run_scan(DemoIssueSource(now=FROZEN_NOW), config, now=FROZEN_NOW)

    def test_it_scans_every_open_demo_issue(self, result: ScanResult) -> None:
        assert result.issue_count == 60
        assert all(i.is_open for i in result.issues)

    def test_all_five_checks_contribute(self, result: ScanResult) -> None:
        assert set(findings_by_check(result)) == {
            "unassigned",
            "overdue",
            "stale",
            "high_priority_stale",
            "status_age",
        }
        assert all(v > 0 for v in findings_by_check(result).values())

    def test_nothing_is_skipped_on_a_full_scan(self, result: ScanResult) -> None:
        assert result.skipped_checks == []

    def test_counts_are_internally_consistent(self, result: ScanResult) -> None:
        assert sum(result.counts.values()) == result.finding_count
        assert sum(findings_by_check(result).values()) == result.finding_count
        assert result.clean_issue_count == result.issue_count - len(result.flagged_issue_keys)
        assert 0 < result.clean_issue_count < result.issue_count

    def test_some_issues_are_clean_and_some_carry_several_findings(
        self, result: ScanResult
    ) -> None:
        """A scan where everything is flagged is as useless as one where nothing is."""
        per_issue: dict[str, int] = {}
        for finding in result.findings:
            per_issue[finding.issue_key] = per_issue.get(finding.issue_key, 0) + 1
        assert result.clean_issue_count > 0
        assert max(per_issue.values()) >= 3

    def test_the_excluded_status_issue_is_not_flagged_for_status_age(
        self, result: ScanResult
    ) -> None:
        checks_for_49 = {f.check_id for f in result.findings if f.issue_key == "DEMO-49"}
        assert "status_age" not in checks_for_49

    def test_the_closed_issue_never_appears(self, result: ScanResult) -> None:
        assert "DEMO-51" not in {i.key for i in result.issues}
        assert "DEMO-51" not in result.flagged_issue_keys

    def test_every_finding_links_back_to_jira(self, result: ScanResult) -> None:
        for finding in result.findings:
            assert "/browse/" in finding.issue_url
            assert finding.issue_url.endswith(finding.issue_key)

    def test_the_scan_id_matches_the_project_and_date(self, result: ScanResult) -> None:
        assert result.scan_id.startswith("20261104-DEMO-")

    def test_no_credential_shaped_text_reaches_the_result(self, result: ScanResult) -> None:
        dumped = result.model_dump_json()
        for leak in ("Authorization", "Basic ", "Bearer ", "api_token"):
            assert leak not in dumped

    def test_a_no_changelog_scan_of_the_demo_data_is_honest(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        result = run_scan(DemoIssueSource(now=FROZEN_NOW), config, now=FROZEN_NOW)
        assert result.skipped_checks == ["status_age"]
        assert not any(f.check_id == "status_age" for f in result.findings)
        assert result.finding_count > 0


class TestProtocolCompliance:
    def test_the_fake_source_satisfies_the_protocol(self) -> None:
        assert isinstance(FakeSource(), IssueSource)

    def test_a_source_without_a_name_degrades_gracefully(self, config: ScanConfig) -> None:
        class Nameless:
            def fetch_issues(self, config: ScanConfig) -> list[IssueRecord]:
                return []

            def fetch_status_history(
                self, issue_keys: Sequence[str]
            ) -> dict[str, list[StatusChange]]:
                return {}

        assert run_scan(Nameless(), config, now=FROZEN_NOW).source_name == "unknown"  # type: ignore[arg-type]


def test_a_live_clock_is_used_when_none_is_injected(config: ScanConfig) -> None:
    before = datetime.now(tz=LUSAKA)
    result = run_scan(FakeSource([make_issue(now=before)]), config)
    assert before <= result.started_at <= datetime.now(tz=LUSAKA)
