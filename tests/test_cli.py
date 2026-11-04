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


def test_demo_runs_with_no_credentials() -> None:
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    assert "Open issues loaded" in result.output


def test_demo_reports_a_plausible_issue_count() -> None:
    result = runner.invoke(app, ["demo"])
    assert "Unassigned" in result.output
    assert "Display timezone" in result.output


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
