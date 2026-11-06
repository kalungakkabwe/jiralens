"""Typer command-line interface.

The CLI collects input, calls ``run_scan`` and renders the returned ``ScanResult``.
It contains no audit logic. ``_render_result`` is shared so that the live ``scan``
command in Phase 7 prints exactly what ``demo`` prints today.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from jiralens import __version__
from jiralens.config import load_settings
from jiralens.exceptions import JiraLensError
from jiralens.jira.client import JiraClient
from jiralens.logging_config import configure_logging
from jiralens.models import ScanConfig, ScanResult
from jiralens.reports import ALL_FORMATS, write_reports
from jiralens.scanner import ScanProgress, findings_by_check, run_scan
from jiralens.sources.demo import DEMO_PROJECT_KEY, DemoIssueSource
from jiralens.sources.jira_source import JiraIssueSource

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Read-only Jira Cloud project health auditor. JiraLens never writes to Jira.",
)
console = Console()
err_console = Console(stderr=True)

SEVERITY_STYLE = {"high": "bold red", "warning": "yellow", "info": "cyan"}


def _fail(exc: JiraLensError) -> None:
    err_console.print(f"[bold red]{type(exc).__name__}[/]: {exc}")
    raise typer.Exit(code=exc.exit_code)


def _build_config(
    project: str,
    *,
    jql: str | None,
    stale_days: int,
    high_priority_stale_days: int,
    status_age_days: int,
    exclude_status: list[str],
    timezone: str,
    no_changelog: bool,
) -> ScanConfig:
    return ScanConfig(
        project_key=project,
        jql=jql,
        stale_days=stale_days,
        high_priority_stale_days=high_priority_stale_days,
        status_age_days=status_age_days,
        excluded_statuses=frozenset(exclude_status),
        timezone=timezone,
        include_changelog=not no_changelog,
    )


def _render_result(result: ScanResult, *, top: int) -> None:
    """Print a scan result. Shared by demo and, from Phase 7, the live scan."""
    overview = Table(title=f"Scan {result.scan_id}", title_justify="left", show_header=False)
    overview.add_column("Metric")
    overview.add_column("Value", justify="right")
    counts = result.counts
    overview.add_row("Issues scanned", str(result.issue_count))
    overview.add_row("Findings", str(result.finding_count))
    overview.add_row("  High", f"[bold red]{counts['high']}[/]")
    overview.add_row("  Warning", f"[yellow]{counts['warning']}[/]")
    overview.add_row("  Informational", str(counts["info"]))
    overview.add_row("Issues with findings", str(len(result.flagged_issue_keys)))
    overview.add_row("Issues with no findings", str(result.clean_issue_count))
    overview.add_row("Source", result.source_name)
    overview.add_row("Elapsed", f"{result.duration_seconds:.2f}s")
    console.print(overview)

    per_check = Table(title="By check", title_justify="left")
    per_check.add_column("Check")
    per_check.add_column("Findings", justify="right")
    for check_id, count in findings_by_check(result).items():
        per_check.add_row(check_id, str(count))
    for skipped in result.skipped_checks:
        per_check.add_row(f"[dim]{skipped}[/]", "[dim]skipped[/]")
    console.print(per_check)

    if result.findings:
        table = Table(title=f"Top {min(top, result.finding_count)} findings", title_justify="left")
        for column in ("Severity", "Check", "Issue", "Reason"):
            table.add_column(column, overflow="fold")
        for finding in result.findings[:top]:
            style = SEVERITY_STYLE[finding.severity]
            table.add_row(
                f"[{style}]{finding.severity}[/]",
                finding.check_id,
                finding.issue_key,
                finding.reason,
            )
        console.print(table)

    if result.warnings:
        console.print("\n[bold]Scope and limitations[/]")
        for warning in result.warnings:
            console.print(f"  • {warning}")


def _render_written(written: dict[str, Path]) -> None:
    """List the report files produced, so the user knows exactly what to open."""
    if not written:
        return
    console.print("\n[bold]Reports written[/]")
    for kind, path in written.items():
        size = path.stat().st_size
        console.print(f"  {kind:<5} {path}  [dim]({size:,} bytes)[/]")


@app.command()
def version() -> None:
    """Print the JiraLens version."""
    console.print(f"jiralens {__version__}")


@app.command(name="connection-test")
def connection_test() -> None:
    """Confirm credentials against GET /rest/api/3/myself."""
    try:
        settings = load_settings()
        configure_logging(settings.jiralens_log_level, settings.secret_values())
        with JiraClient(settings) as client:
            user = client.test_connection()
            info = client.get_server_info()
    except JiraLensError as exc:
        err_console.print("[bold red]Connection failed[/]")
        err_console.print(f"Reason: {exc}")
        err_console.print("[dim]No data was retrieved.[/]")
        raise typer.Exit(code=exc.exit_code) from None

    table = Table(show_header=False, title="Connection successful", title_justify="left")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Site", info.server_title or settings.site_url or "—")
    table.add_row("API base", settings.api_base_url or "—")
    table.add_row("Token type", "scoped" if settings.uses_scoped_token else "unscoped (classic)")
    table.add_row("Authenticated as", user.display_name)
    table.add_row("Account type", user.account_type or "—")
    table.add_row("Read-only mode", "enabled")
    console.print(table)


@app.command()
def scan(
    project: str = typer.Option(..., "--project", "-p", help="Jira project key."),
    jql: str | None = typer.Option(None, "--jql", help="Custom bounded JQL."),
    stale_days: int = typer.Option(30, "--stale-days"),
    high_priority_stale_days: int = typer.Option(7, "--high-priority-stale-days"),
    status_age_days: int = typer.Option(14, "--status-age-days"),
    exclude_status: list[str] = typer.Option(
        [], "--exclude-status", help="Status to exclude from the status-age check. Repeatable."
    ),
    no_changelog: bool = typer.Option(
        False, "--no-changelog", help="Faster scan; skips the current-status-age check."
    ),
    top: int = typer.Option(10, "--top", help="How many findings to print."),
    output: str = typer.Option("reports", "--output", "-o", help="Output folder."),
) -> None:
    """Scan a live Jira project. Requires credentials in .env."""
    try:
        settings = load_settings()
        configure_logging(settings.jiralens_log_level, settings.secret_values())
        config = _build_config(
            project,
            jql=jql,
            stale_days=stale_days,
            high_priority_stale_days=high_priority_stale_days,
            status_age_days=status_age_days,
            exclude_status=exclude_status,
            timezone=settings.jiralens_timezone,
            no_changelog=no_changelog,
        )
        now = datetime.now(tz=config.tzinfo)
        with JiraClient(settings) as client:
            result = run_scan(
                JiraIssueSource(client),
                config,
                now=now,
                on_progress=lambda p: err_console.print(f"[dim]{p}[/]"),
            )
        written = write_reports(result, output) if output else {}
    except JiraLensError as exc:
        _fail(exc)

    _render_result(result, top=top)
    _render_written(written)


@app.command()
def demo(
    stale_days: int = typer.Option(30, "--stale-days"),
    high_priority_stale_days: int = typer.Option(7, "--high-priority-stale-days"),
    status_age_days: int = typer.Option(14, "--status-age-days"),
    exclude_status: list[str] = typer.Option(
        [], "--exclude-status", help="Status to exclude from the status-age check. Repeatable."
    ),
    no_changelog: bool = typer.Option(
        False, "--no-changelog", help="Skip status histories to show the honest-skip behaviour."
    ),
    timezone: str | None = typer.Option(None, "--timezone", help="IANA display timezone."),
    top: int = typer.Option(10, "--top", help="How many findings to print."),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Write reports into this folder."
    ),
    report_format: list[str] = typer.Option(
        list(ALL_FORMATS),
        "--format",
        "-f",
        help="Report format to write. Repeatable. Only used with --output.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print each scan stage."),
) -> None:
    """Run a full audit against the bundled synthetic dataset. No credentials needed."""
    try:
        settings = load_settings()
        configure_logging(settings.jiralens_log_level, settings.secret_values())
        config = _build_config(
            DEMO_PROJECT_KEY,
            jql=None,
            stale_days=stale_days,
            high_priority_stale_days=high_priority_stale_days,
            status_age_days=status_age_days,
            exclude_status=exclude_status,
            timezone=timezone or settings.jiralens_timezone,
            no_changelog=no_changelog,
        )
        now = datetime.now(tz=config.tzinfo)

        def show(progress: ScanProgress) -> None:
            if verbose:
                err_console.print(f"[dim]{progress}[/]")

        result = run_scan(DemoIssueSource(now=now), config, now=now, on_progress=show)
        written = write_reports(result, output, formats=report_format) if output else {}
    except JiraLensError as exc:
        _fail(exc)

    _render_result(result, top=top)
    _render_written(written)
    if not written:
        console.print("\n[dim]Pass --output DIR to write the CSV, HTML and JSON reports.[/]")
    console.print("[dim]For the interactive dashboard: streamlit run streamlit_app.py[/]")


if __name__ == "__main__":  # pragma: no cover
    app()
