"""Typer command-line interface.

The CLI collects input, calls ``run_scan`` and renders the returned ``ScanResult``.
It contains no audit logic. ``_render_result`` is shared so that the live ``scan``
command in Phase 7 prints exactly what ``demo`` prints today.
"""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from jiralens import __version__
from jiralens.config import load_settings
from jiralens.exceptions import JiraLensError
from jiralens.logging_config import configure_logging
from jiralens.models import ScanConfig, ScanResult
from jiralens.scanner import ScanProgress, findings_by_check, run_scan
from jiralens.sources.demo import DEMO_PROJECT_KEY, DemoIssueSource

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
        settings.require_live()
    except JiraLensError as exc:
        _fail(exc)
    err_console.print("[yellow]Not implemented yet — Phase 6 (read-only Jira client).[/]")
    raise typer.Exit(code=1)


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
        settings.require_live()
        _build_config(
            project,
            jql=jql,
            stale_days=stale_days,
            high_priority_stale_days=high_priority_stale_days,
            status_age_days=status_age_days,
            exclude_status=exclude_status,
            timezone=settings.jiralens_timezone,
            no_changelog=no_changelog,
        )
    except JiraLensError as exc:
        _fail(exc)
    err_console.print(
        f"[yellow]Not implemented yet — Phase 7 (live Jira source). Configuration "
        f"validated; reports would be written to {output!r}, and --top {top} applied.[/]"
    )
    raise typer.Exit(code=1)


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
    except JiraLensError as exc:
        _fail(exc)

    _render_result(result, top=top)
    console.print(
        "\n[yellow]Exports arrive in Phase 4 (CSV, HTML, JSON); the dashboard in Phase 8.[/]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
