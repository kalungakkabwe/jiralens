"""HTML export — the executive report.

Rendered through Jinja2 with autoescaping forced on. Note that ``select_autoescape``
would *not* enable escaping for a ``.html.j2`` filename, so it is set explicitly:
this template renders Jira summaries written by other people, and an unescaped
summary containing markup is a stored-XSS vector in a file an administrator opens.

The output embeds no scripts and loads nothing from the network, so the report opens
correctly from a local folder, an email attachment or a printer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from jiralens.checks import default_checks
from jiralens.exceptions import ReportGenerationError
from jiralens.models import ScanResult
from jiralens.reports.rows import build_rows, group_by
from jiralens.scanner import findings_by_check

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "report.html.j2"

#: Guidance surfaced in the methodology section, where an administrator tuning the
#: tool will actually read it.
METHODOLOGY_NOTES: tuple[str, ...] = (
    "All ages are counted in whole calendar days in the display timezone shown under "
    "Scan metadata — not business days, and not an SLA calendar.",
    "Thresholds are exclusive: at a 30-day stale threshold, an issue last updated "
    "exactly 30 days ago is not reported; 31 days is.",
    "An issue due today is not overdue.",
    "Jira's 'updated' field moves for automation runs and administrative edits that "
    "represent no human progress, and does not move when someone works on an issue "
    "without touching it. Treat the stale checks as inactivity signals, not proof.",
    "Current-status age is measured from the most recent status transition in the "
    "issue's changelog. Where an issue has never transitioned, it is measured from "
    "the creation date instead.",
    "Issues sitting in a backlog status such as 'To Do' will be reported for status "
    "age once they pass the threshold. If a long-lived backlog is normal here, add "
    "that status to the excluded list so the report shows work that is genuinely "
    "stuck in flight rather than work that has not started.",
)

LIMITATIONS: tuple[str, ...] = (
    "Findings cover only the issues the authenticated account can browse. Issue-level "
    "security or project permissions may hide work from this scan entirely.",
    "JiraLens is read-only. It made no change to any issue, field, workflow or "
    "configuration while producing this report.",
    "Issue descriptions, comments, attachments and worklogs are never retrieved, so "
    "none of that content appears here and none of it was held in memory.",
    "There is no health score. A single number would depend on arbitrary weights and "
    "would read as more precise than the underlying data supports.",
)


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,  # explicit: select_autoescape ignores a .html.j2 suffix
        undefined=StrictUndefined,  # a typo in the template fails loudly, not silently
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_context(result: ScanResult) -> dict[str, Any]:
    """Everything the template needs. No audit logic, only shaping."""
    rows = build_rows(result, escape=False)
    tz = ZoneInfo(result.config.timezone)
    descriptions = {c.id: c.description for c in default_checks()}

    return {
        "result": result,
        "config": result.config,
        "counts": result.counts,
        "counts_by_check": findings_by_check(result),
        "check_names": {c.id: c.name for c in default_checks()},
        "check_descriptions": descriptions,
        "rows": rows,
        "high_rows": [r for r in rows if r["severity"] == "high"],
        "warning_rows": [r for r in rows if r["severity"] == "warning"],
        "info_rows": [r for r in rows if r["severity"] == "info"],
        "by_check": group_by(rows, "check_name"),
        "by_assignee": group_by(rows, "assignee"),
        "flagged_count": len(result.flagged_issue_keys),
        "generated_local": result.started_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z"),
        "methodology_notes": METHODOLOGY_NOTES,
        "limitations": LIMITATIONS,
    }


def render_html(result: ScanResult) -> str:
    """Render the report to a string.

    Raises:
        ReportGenerationError: if the template is missing or fails to render.
    """
    try:
        template = _environment().get_template(TEMPLATE_NAME)
        return template.render(**build_context(result))
    except Exception as exc:  # jinja2.TemplateError and anything the template raises
        raise ReportGenerationError(f"Could not render the HTML report: {exc}") from exc


def write_html(result: ScanResult, path: Path) -> Path:
    """Write the executive HTML report. Returns the path written."""
    content = render_html(result)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ReportGenerationError(f"Could not write the HTML report to {path}: {exc}") from exc
    return path


__all__ = [
    "LIMITATIONS",
    "METHODOLOGY_NOTES",
    "TEMPLATE_DIR",
    "build_context",
    "render_html",
    "write_html",
]
