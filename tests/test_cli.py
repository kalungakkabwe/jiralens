"""CLI wiring.

The important assertion here is that ``jiralens demo`` works with no credentials at
all, and that the commands needing Jira fail with a readable message rather than a
traceback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jiralens import __version__
from jiralens.cli import app

runner = CliRunner()

CREDENTIAL_ENV = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


def flat(output: str) -> str:
    """Collapse whitespace so assertions test content, not rich's line wrapping."""
    return " ".join(output.split())


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run in an empty directory so no developer's real .env is read."""
    monkeypatch.chdir(tmp_path)
    for name in CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)


def test_version_prints_the_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_bare_invocation_shows_help_rather_than_a_traceback() -> None:
    """Click exits 2 when no command is given; what matters is that help is shown."""
    result = runner.invoke(app, [])
    assert "Usage" in result.output
    assert "Traceback" not in result.output


class TestDemoScan:
    def test_it_runs_a_full_scan_with_no_credentials(self) -> None:
        result = runner.invoke(app, ["demo"])
        assert result.exit_code == 0, result.output
        assert "Issues scanned" in result.output
        assert "Findings" in result.output

    def test_it_reports_every_check(self) -> None:
        result = runner.invoke(app, ["demo"])
        for check_id in ("unassigned", "overdue", "stale", "high_priority_stale", "status_age"):
            assert check_id in result.output

    def test_it_states_the_permission_caveat(self) -> None:
        """Every scan must disclose that it only sees what the account can browse."""
        assert "permitted to browse" in flat(runner.invoke(app, ["demo"]).output)

    def test_it_prints_both_flagged_and_clean_issue_counts(self) -> None:
        output = runner.invoke(app, ["demo"]).output
        assert "Issues with findings" in output
        assert "Issues with no findings" in output

    def test_no_changelog_marks_the_check_skipped_rather_than_zero(self) -> None:
        result = runner.invoke(app, ["demo", "--no-changelog"])
        assert result.exit_code == 0, result.output
        assert "skipped" in flat(result.output)
        assert "Status history was not retrieved" in flat(result.output)

    def test_excluding_a_status_is_disclosed_in_the_output(self) -> None:
        result = runner.invoke(app, ["demo", "--exclude-status", "Waiting for Customer"])
        assert result.exit_code == 0, result.output
        assert "excluded from the current-status-age check" in flat(result.output)

    def test_top_limits_the_findings_table(self) -> None:
        assert runner.invoke(app, ["demo", "--top", "3"]).output.count("Top 3 findings") == 1

    def test_tighter_thresholds_produce_more_findings(self) -> None:
        """A crude but real check that the CLI options reach the engine."""
        import re

        def finding_count(args: list[str]) -> int:
            output = runner.invoke(app, args).output
            match = re.search(r"Findings\s+│\s+(\d+)", output)
            assert match, output
            return int(match.group(1))

        assert finding_count(["demo", "--stale-days", "5"]) > finding_count(
            ["demo", "--stale-days", "200"]
        )

    def test_verbose_prints_the_scan_stages(self) -> None:
        output = runner.invoke(app, ["demo", "--verbose"]).output
        assert "Retrieving issues" in output
        assert "Complete" in output

    def test_an_invalid_timezone_fails_readably(self) -> None:
        result = runner.invoke(app, ["demo", "--timezone", "Mars/Olympus"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output


class TestMissingCredentialsAreReadable:
    def test_connection_test_names_the_missing_variables(self) -> None:
        result = runner.invoke(app, ["connection-test"])
        assert result.exit_code == 2  # ConfigurationError
        assert "JIRA_BASE_URL" in result.output
        assert "Traceback" not in result.output

    def test_scan_names_the_missing_variables(self) -> None:
        result = runner.invoke(app, ["scan", "--project", "ITS"])
        assert result.exit_code == 2
        assert "JIRA_API_TOKEN" in result.output
        assert "Traceback" not in result.output

    def test_scan_rejects_a_bad_project_key_before_touching_jira(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in zip(
            CREDENTIAL_ENV,
            ("https://acme.atlassian.net", "a@b.com", "token-value-1234"),
            strict=True,
        ):
            monkeypatch.setenv(name, value)
        result = runner.invoke(app, ["scan", "--project", "ITS-1"])
        assert result.exit_code != 0
        assert "Traceback" not in result.output

    def test_live_disabled_is_explained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JIRALENS_LIVE_JIRA_DISABLED", "true")
        result = runner.invoke(app, ["connection-test"])
        assert result.exit_code == 2
        assert "demo mode" in result.output


def test_no_command_writes_to_jira() -> None:
    """Sanity check on the surface area: no write verbs are exposed as commands."""
    names = {c.name for c in app.registered_commands}
    for forbidden in ("create", "update", "transition", "comment", "delete", "edit"):
        assert not any(forbidden in (name or "") for name in names), names
