"""Report exporters.

Two of these tests exist because of specific ways an audit report can hurt someone:
a Jira summary that executes as a formula when the CSV is opened in Excel, and one
that executes as markup when the HTML report is opened in a browser.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from jiralens.exceptions import ReportGenerationError
from jiralens.models import ScanConfig, ScanResult
from jiralens.reports import ALL_FORMATS, write_reports
from jiralens.reports.csv_report import build_dataframe, write_csv
from jiralens.reports.html_report import render_html, write_html
from jiralens.reports.json_report import SCHEMA_VERSION, build_summary, write_json
from jiralens.reports.rows import CSV_COLUMNS, build_rows, escape_formula, group_by
from jiralens.scanner import run_scan
from jiralens.sources.demo import DemoIssueSource
from tests.conftest import FROZEN_NOW
from tests.factories import make_issue
from tests.test_scanner import FakeSource


@pytest.fixture(scope="module")
def result() -> ScanResult:
    """Module-scoped: the scan is deterministic and no test mutates the result, so
    running it 40 times only slows the suite down."""
    config = ScanConfig(project_key="DEMO", excluded_statuses=frozenset({"Waiting for Customer"}))
    return run_scan(DemoIssueSource(now=FROZEN_NOW), config, now=FROZEN_NOW)


@pytest.fixture
def empty_result() -> ScanResult:
    return run_scan(FakeSource([]), ScanConfig(project_key="EMPTY"), now=FROZEN_NOW)


# --------------------------------------------------------------------------------
# Row building
# --------------------------------------------------------------------------------


class TestRows:
    def test_one_row_per_finding(self, result: ScanResult) -> None:
        assert len(build_rows(result)) == result.finding_count

    def test_rows_carry_every_published_column(self, result: ScanResult) -> None:
        assert set(build_rows(result)[0]) == set(CSV_COLUMNS)

    def test_issue_context_is_joined_in(self, result: ScanResult) -> None:
        row = next(r for r in build_rows(result) if r["issue_key"] == "DEMO-19")
        assert row["assignee"]
        assert row["status"]
        assert row["priority"] == "Highest"
        assert row["created"]

    def test_unassigned_work_is_labelled_not_blank(self, result: ScanResult) -> None:
        rows = [r for r in build_rows(result) if r["check_id"] == "unassigned"]
        assert rows
        assert all(r["assignee"] == "Unassigned" for r in rows)

    def test_timestamps_are_rendered_in_the_display_timezone(self) -> None:
        config = ScanConfig(project_key="DEMO", timezone="America/Chicago")
        res = run_scan(FakeSource([make_issue(assignee=None)]), config, now=FROZEN_NOW)
        assert build_rows(res)[0]["scan_time"].endswith("-06:00")

    def test_a_missing_due_date_is_an_empty_cell_not_the_word_none(
        self, result: ScanResult
    ) -> None:
        for row in build_rows(result):
            assert row["due_date"] != "None"

    def test_rows_follow_the_scans_severity_order(self, result: ScanResult) -> None:
        severities = [r["severity"] for r in build_rows(result)]
        order = {"high": 0, "warning": 1, "info": 2}
        assert severities == sorted(severities, key=lambda s: order[s])


class TestFormulaEscaping:
    @pytest.mark.parametrize("payload", ["=1+1", "+1", "-1", "@SUM(A1)", "\tcmd", "\rcmd"])
    def test_every_trigger_character_is_neutralised(self, payload: str) -> None:
        assert escape_formula(payload) == "'" + payload

    def test_ordinary_text_is_untouched(self) -> None:
        assert escape_formula("Renew the certificate") == "Renew the certificate"

    def test_numbers_pass_through_unchanged(self) -> None:
        """Escaping a negative number would turn it into text and break every total."""
        assert escape_formula(-5) == -5
        assert escape_formula(46) == 46
        assert escape_formula(None) is None

    def test_the_malicious_demo_summary_is_escaped_in_the_csv(self, result: ScanResult) -> None:
        rows = [r for r in build_rows(result) if r["issue_key"] == "DEMO-59"]
        assert rows, "the CSV-injection fixture produced no finding"
        assert rows[0]["summary"].startswith("'=")

    def test_html_rows_are_not_apostrophe_prefixed(self, result: ScanResult) -> None:
        """Jinja2 escaping handles HTML; an apostrophe there would be visible noise."""
        rows = [r for r in build_rows(result, escape=False) if r["issue_key"] == "DEMO-59"]
        assert rows[0]["summary"].startswith("=")


class TestGrouping:
    def test_counts_split_by_severity_and_total(self, result: ScanResult) -> None:
        grouped = group_by(build_rows(result), "check_name")
        for bucket in grouped.values():
            assert bucket["total"] == bucket["high"] + bucket["warning"] + bucket["info"]

    def test_totals_reconcile_with_the_scan(self, result: ScanResult) -> None:
        grouped = group_by(build_rows(result), "assignee")
        assert sum(b["total"] for b in grouped.values()) == result.finding_count

    def test_worst_first_ordering(self, result: ScanResult) -> None:
        highs = [b["high"] for b in group_by(build_rows(result), "check_name").values()]
        assert highs == sorted(highs, reverse=True)

    def test_a_blank_key_becomes_a_dash(self) -> None:
        assert "—" in group_by([{"assignee": "", "severity": "high"}], "assignee")


# --------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------


class TestCsv:
    def test_column_order_is_exactly_the_published_contract(self, result: ScanResult) -> None:
        assert tuple(build_dataframe(result).columns) == CSV_COLUMNS

    def test_row_count_matches_the_finding_count(self, result: ScanResult) -> None:
        assert len(build_dataframe(result)) == result.finding_count

    def test_an_empty_scan_still_writes_a_usable_header(
        self, empty_result: ScanResult, tmp_path: Path
    ) -> None:
        path = write_csv(empty_result, tmp_path / "f.csv")
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        assert rows == [list(CSV_COLUMNS)]

    def test_the_file_round_trips_through_a_csv_reader(
        self, result: ScanResult, tmp_path: Path
    ) -> None:
        path = write_csv(result, tmp_path / "f.csv")
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == result.finding_count
        assert rows[0]["issue_key"].startswith("DEMO-")

    def test_a_summary_with_a_comma_survives_quoting(self, tmp_path: Path) -> None:
        issue = make_issue(assignee=None, summary="Renew cert, then rotate the key")
        res = run_scan(FakeSource([issue]), ScanConfig(project_key="DEMO"), now=FROZEN_NOW)
        path = write_csv(res, tmp_path / "f.csv")
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["summary"] == "Renew cert, then rotate the key"

    def test_nested_directories_are_created(self, result: ScanResult, tmp_path: Path) -> None:
        path = write_csv(result, tmp_path / "a" / "b" / "f.csv")
        assert path.exists()

    def test_an_unwritable_path_raises_a_report_error(
        self, result: ScanResult, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        with pytest.raises(ReportGenerationError, match="CSV"):
            write_csv(result, blocker / "f.csv")


# --------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------


class TestJson:
    def test_it_declares_a_schema_version(self, result: ScanResult) -> None:
        assert build_summary(result)["schema_version"] == SCHEMA_VERSION

    def test_counts_agree_with_the_scan(self, result: ScanResult) -> None:
        payload = build_summary(result)
        assert payload["issue_count"] == result.issue_count
        assert payload["finding_count"] == result.finding_count
        assert payload["counts"] == result.counts
        assert sum(payload["counts"].values()) == payload["finding_count"]
        assert payload["clean_issue_count"] == result.clean_issue_count

    def test_configuration_is_recorded_so_a_scan_is_reproducible(self, result: ScanResult) -> None:
        config = build_summary(result)["configuration"]
        assert config["stale_days"] == 30
        assert config["timezone"] == "Africa/Lusaka"
        assert "Waiting for Customer" in config["excluded_statuses"]
        assert config["jql"].startswith("project = DEMO")

    def test_findings_are_included_and_issues_are_not_by_default(self, result: ScanResult) -> None:
        payload = build_summary(result)
        assert len(payload["findings"]) == result.finding_count
        assert "issues" not in payload

    def test_issues_can_be_opted_into(self, result: ScanResult) -> None:
        payload = build_summary(result, include_issues=True)
        assert len(payload["issues"]) == result.issue_count

    def test_it_states_that_the_tool_is_read_only(self, result: ScanResult) -> None:
        assert build_summary(result)["read_only"] is True

    def test_skipped_checks_are_reported_and_omitted_from_counts(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        res = run_scan(DemoIssueSource(now=FROZEN_NOW), config, now=FROZEN_NOW)
        payload = build_summary(res)
        assert payload["skipped_checks"] == ["status_age"]
        assert "status_age" not in payload["counts_by_check"]

    def test_the_file_is_valid_json(self, result: ScanResult, tmp_path: Path) -> None:
        path = write_json(result, tmp_path / "s.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["scan_id"] == result.scan_id

    def test_no_credential_shaped_text_appears(self, result: ScanResult) -> None:
        text = json.dumps(build_summary(result, include_issues=True))
        for leak in ("Authorization", "Basic ", "Bearer ", "api_token", "ATATT"):
            assert leak not in text

    def test_an_empty_scan_serialises_cleanly(
        self, empty_result: ScanResult, tmp_path: Path
    ) -> None:
        payload = json.loads(write_json(empty_result, tmp_path / "s.json").read_text())
        assert payload["finding_count"] == 0
        assert payload["findings"] == []


# --------------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------------


class TestHtml:
    def test_it_renders_a_complete_document(self, result: ScanResult) -> None:
        html = render_html(result)
        assert html.lstrip().startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")

    def test_every_required_section_is_present(self, result: ScanResult) -> None:
        html = render_html(result)
        for section in (
            "Executive summary",
            "Scope and thresholds",
            "High-risk findings",
            "Warning findings",
            "Findings by check",
            "Findings by assignee",
            "Methodology",
            "Limitations",
            "Scan metadata",
        ):
            assert section in html, section

    def test_the_headline_counts_appear(self, result: ScanResult) -> None:
        html = render_html(result)
        assert f">{result.issue_count}</div>" in html
        assert f">{result.counts['high']}</div>" in html

    def test_findings_link_back_to_jira(self, result: ScanResult) -> None:
        html = render_html(result)
        assert 'rel="noopener noreferrer"' in html
        assert "/browse/DEMO-" in html

    def test_it_states_that_the_scan_was_read_only(self, result: ScanResult) -> None:
        assert "no change was made to jira" in render_html(result).lower()

    def test_the_permission_caveat_is_carried_into_the_report(self, result: ScanResult) -> None:
        assert "permitted to browse" in render_html(result)

    def test_the_backlog_status_guidance_reaches_the_methodology(self, result: ScanResult) -> None:
        """The tuning advice belongs where an administrator will read it."""
        assert "backlog status" in render_html(result)

    def test_it_loads_nothing_from_the_network(self, result: ScanResult) -> None:
        """The report must open correctly from a local folder, an email or a printer."""
        html = render_html(result)
        for remote in ("http://", "src=", "<link", "@import"):
            assert remote not in html

    def test_it_contains_no_executable_script(self, result: ScanResult) -> None:
        assert "<script" not in render_html(result).lower()

    def test_a_markup_payload_in_a_summary_is_escaped(self, result: ScanResult) -> None:
        """DEMO-60's summary contains a script tag; it must render as text."""
        html = render_html(result)
        assert "<script>alert" not in html
        assert "&lt;script&gt;alert" in html

    def test_a_skipped_check_is_called_out_rather_than_shown_as_zero(self) -> None:
        config = ScanConfig(project_key="DEMO", include_changelog=False)
        res = run_scan(DemoIssueSource(now=FROZEN_NOW), config, now=FROZEN_NOW)
        html = render_html(res)
        assert "Checks skipped" in html
        assert "not a finding of zero" in html

    def test_an_empty_scan_renders_without_empty_tables(self, empty_result: ScanResult) -> None:
        html = render_html(empty_result)
        assert "No high-severity findings" in html
        assert "No warnings in this scan" in html

    def test_a_template_typo_would_fail_loudly(self) -> None:
        """StrictUndefined is set, so a renamed context key cannot render as blank."""
        from jiralens.reports.html_report import _environment

        template = _environment().from_string("{{ nonexistent_key }}")
        with pytest.raises(Exception, match="undefined"):
            template.render()

    def test_the_file_is_written_as_utf8(self, result: ScanResult, tmp_path: Path) -> None:
        path = write_html(result, tmp_path / "r.html")
        assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


# --------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------


class TestWriteReports:
    def test_all_three_formats_are_written(self, result: ScanResult, tmp_path: Path) -> None:
        written = write_reports(result, tmp_path)
        assert set(written) == set(ALL_FORMATS)
        for path in written.values():
            assert path.exists()
            assert path.stat().st_size > 0

    def test_filenames_carry_the_scan_id(self, result: ScanResult, tmp_path: Path) -> None:
        written = write_reports(result, tmp_path)
        for path in written.values():
            assert path.name.startswith(result.scan_id)

    def test_a_subset_of_formats_is_honoured(self, result: ScanResult, tmp_path: Path) -> None:
        written = write_reports(result, tmp_path, formats=["json"])
        assert set(written) == {"json"}
        assert not list(tmp_path.glob("*.csv"))

    def test_an_unknown_format_is_refused_with_the_valid_options(
        self, result: ScanResult, tmp_path: Path
    ) -> None:
        with pytest.raises(ReportGenerationError, match="pdf"):
            write_reports(result, tmp_path, formats=["pdf"])

    def test_the_output_directory_is_created(self, result: ScanResult, tmp_path: Path) -> None:
        written = write_reports(result, tmp_path / "nested" / "reports")
        assert all(p.exists() for p in written.values())

    def test_a_string_path_works_as_well_as_a_path(
        self, result: ScanResult, tmp_path: Path
    ) -> None:
        assert write_reports(result, str(tmp_path), formats=["csv"])["csv"].exists()

    def test_the_three_formats_agree_on_the_finding_count(
        self, result: ScanResult, tmp_path: Path
    ) -> None:
        """The single most important property: no format can contradict another."""
        written = write_reports(result, tmp_path)

        with written["csv"].open(newline="", encoding="utf-8") as handle:
            csv_rows = list(csv.DictReader(handle))
        payload = json.loads(written["json"].read_text(encoding="utf-8"))
        html = written["html"].read_text(encoding="utf-8")

        assert len(csv_rows) == payload["finding_count"] == result.finding_count
        assert f"<dd>{result.finding_count}</dd>" in html

    def test_no_report_contains_a_credential(self, result: ScanResult, tmp_path: Path) -> None:
        for path in write_reports(result, tmp_path).values():
            text = path.read_text(encoding="utf-8")
            for leak in ("Authorization", "Bearer ", "api_token", "ATATT"):
                assert leak not in text, path.name

    def test_two_scans_do_not_overwrite_each_other(
        self, result: ScanResult, tmp_path: Path
    ) -> None:
        from datetime import timedelta

        later = run_scan(
            DemoIssueSource(now=FROZEN_NOW),
            result.config,
            now=FROZEN_NOW + timedelta(hours=1),
        )
        write_reports(result, tmp_path, formats=["json"])
        write_reports(later, tmp_path, formats=["json"])
        assert len(list(tmp_path.glob("*summary.json"))) == 2
