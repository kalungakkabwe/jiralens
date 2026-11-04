"""Typer command-line interface.

The CLI collects input, calls the scan service and renders the result. It contains
no audit logic — that lives in ``jiralens.checks`` and ``jiralens.scanner``.
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
from jiralens.models import ScanConfig
from jiralens.sources.demo import DEMO_PROJECT_KEY, DemoIssueSource

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Read-only Jira Cloud project health auditor. JiraLens never writes to Jira.",
)
console = Console()
err_console = Console(stderr=True)


def _fail(exc: JiraLensError) -> None:
    err_console.print(f"[bold red]{type(exc).__name__}[/]: {exc}")
    raise typer.Exit(code=exc.exit_code)


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
    output: str = typer.Option("reports", "--output", "-o", help="Output folder."),
) -> None:
    """Scan a live Jira project. Requires credentials in .env."""
    try:
        settings = load_settings()
        configure_logging(settings.jiralens_log_level, settings.secret_values())
        settings.require_live()
        ScanConfig(
            project_key=project,
            jql=jql,
            stale_days=stale_days,
            high_priority_stale_days=high_priority_stale_days,
            status_age_days=status_age_days,
            timezone=settings.jiralens_timezone,
        )
    except JiraLensError as exc:
        _fail(exc)
    err_console.print(
        f"[yellow]Not implemented yet — Phase 7 (live scan). "
        f"Config validated; output would go to {output!r}.[/]"
    )
    raise typer.Exit(code=1)


@app.command()
def demo(
    stale_days: int = typer.Option(30, "--stale-days"),
    status_age_days: int = typer.Option(14, "--status-age-days"),
) -> None:
    """Load the synthetic dataset through the real pipeline. No credentials needed."""
    try:
        settings = load_settings()
        configure_logging(settings.jiralens_log_level, settings.secret_values())
        config = ScanConfig(
            project_key=DEMO_PROJECT_KEY,
            stale_days=stale_days,
            status_age_days=status_age_days,
            timezone=settings.jiralens_timezone,
        )
        now = datetime.now(tz=config.tzinfo)
        source = DemoIssueSource(now=now)
        issues = source.fetch_issues(config)
        histories = source.fetch_status_history([i.key for i in issues])
    except JiraLensError as exc:
        _fail(exc)

    table = Table(title=f"Demo dataset — loaded {now:%Y-%m-%d %H:%M %Z}", title_justify="left")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Open issues loaded", str(len(issues)))
    table.add_row("Issues with status history", str(len(histories)))
    table.add_row("Unassigned", str(sum(1 for i in issues if i.assignee_account_id is None)))
    table.add_row("With a due date", str(sum(1 for i in issues if i.due_date is not None)))
    table.add_row("Display timezone", config.timezone)
    console.print(table)
    console.print(
        "[yellow]Checks and reports arrive in Phases 2–4. "
        "This command currently proves the demo source and normaliser wiring.[/]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
