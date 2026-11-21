"""Scan orchestration.

``run_scan`` is the single entry point every interface calls. The CLI and the
Streamlit app collect input, call this, and render the returned ``ScanResult`` — they
contain no audit logic, which is what keeps the engine testable and the dashboard
replaceable.

It depends on the ``IssueSource`` protocol rather than on ``JiraClient``, so the same
function serves demo mode and a live Jira scan.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jiralens.checks import default_checks
from jiralens.checks.base import AuditCheck
from jiralens.exceptions import JiraApiError
from jiralens.logging_config import get_logger
from jiralens.models import (
    Finding,
    IssueRecord,
    ScanConfig,
    ScanContext,
    ScanResult,
    StatusChange,
)
from jiralens.sources import IssueSource

logger = get_logger("jiralens.scanner")

#: Stated on every scan. Findings can only ever cover what the account can see.
PERMISSION_CAVEAT = (
    "Results cover only the issues the authenticated account is permitted to browse. "
    "Issue-level security or project permissions may hide work from this scan."
)


@dataclass(frozen=True)
class ScanProgress:
    """One stage transition, for interfaces that show progress.

    The orchestrator knows nothing about Streamlit or rich; it emits these and the
    caller decides what to draw.
    """

    stage: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.stage}{f' — {self.detail}' if self.detail else ''}"


ProgressCallback = Callable[[ScanProgress], None]


def make_scan_id(project_key: str, started_at: datetime, timezone_name: str) -> str:
    """Build a human-readable, sortable scan identifier.

    Format ``YYYYMMDD-PROJECT-HHMMSS`` in the configured display timezone. A
    per-day sequence number (``-001``) would need persisted state to count scans,
    which the MVP deliberately does not have, so the time of day serves instead.
    """
    local = started_at.astimezone(ZoneInfo(timezone_name))
    return f"{local:%Y%m%d}-{project_key}-{local:%H%M%S}"


def latest_status_change(changes: Sequence[StatusChange]) -> StatusChange | None:
    """The most recent transition, found by timestamp rather than by position.

    Never trust the order a changelog arrives in. Taking ``changes[-1]`` works only
    if every page came back sorted, and a paginated history is exactly where that
    assumption breaks — silently, with a plausible-looking wrong answer.
    """
    return max(changes, key=lambda c: c.changed_at) if changes else None


def enrich_status_since(
    issues: Sequence[IssueRecord],
    histories: Mapping[str, Sequence[StatusChange]],
) -> list[IssueRecord]:
    """Attach ``current_status_since`` to each issue. Returns new objects."""
    enriched: list[IssueRecord] = []
    for issue in issues:
        latest = latest_status_change(histories.get(issue.key, ()))
        enriched.append(issue.with_status_since(latest.changed_at if latest else None))
    return enriched


def run_scan(
    source: IssueSource,
    config: ScanConfig,
    checks: Sequence[AuditCheck] | None = None,
    *,
    now: datetime | None = None,
    on_progress: ProgressCallback | None = None,
) -> ScanResult:
    """Run one complete audit and return everything the renderers need.

    Args:
        source: where issues come from — demo fixtures or live Jira.
        config: project, thresholds, timezone, exclusions.
        checks: defaults to the registered MVP check set.
        now: the logical scan instant. Injected so scans are reproducible; defaults
            to the current time in the configured display timezone.
        on_progress: called as each stage begins.

    Raises:
        JiraLensError: if the issue search itself fails. A failure to retrieve status
            histories is downgraded to a warning and recorded in ``skipped_checks``,
            because a partial audit that says which part is missing beats no audit.
    """
    active_checks = list(checks) if checks is not None else default_checks()
    started_at = now or datetime.now(tz=config.tzinfo)
    ctx = ScanContext(now=started_at, config=config)
    clock_start = time.perf_counter()

    warnings: list[str] = [PERMISSION_CAVEAT]
    skipped: list[str] = []

    def progress(stage: str, detail: str = "") -> None:
        # Debug, not info: the user-facing channel is on_progress. Logging stages at
        # info would print them over the CLI's own output on every run.
        logger.debug("scan stage: %s %s", stage, detail)
        if on_progress is not None:
            on_progress(ScanProgress(stage, detail))

    # -- issues -----------------------------------------------------------------
    progress("Retrieving issues", config.effective_jql())
    issues = list(source.fetch_issues(config))
    progress("Retrieved issues", f"{len(issues)} issue(s)")

    if not issues:
        warnings.append(
            "No issues matched the query. Check the project key and the JQL, and "
            "confirm the account can browse this project."
        )

    # -- status histories -------------------------------------------------------
    histories: dict[str, list[StatusChange]] = {}
    changelog_available = config.include_changelog

    if not config.include_changelog:
        warnings.append(
            "Status history was not retrieved, so current-status age could not be "
            "measured. Re-run without --no-changelog for a complete audit."
        )
    elif issues:
        progress("Retrieving status histories", f"{len(issues)} issue(s)")
        try:
            histories = {
                k: list(v) for k, v in source.fetch_status_history([i.key for i in issues]).items()
            }
        except JiraApiError as exc:
            # Degrade honestly rather than reporting ages we never looked up.
            changelog_available = False
            warnings.append(
                f"Status histories could not be retrieved ({exc}). Checks that depend "
                "on them were skipped rather than reported as having no findings."
            )
            logger.warning("changelog retrieval failed: %s", exc)

    # Anything the source needs disclosed — a parse mismatch, unmatched records.
    # Folded in here so a source-level problem cannot stay invisible in the result.
    warnings.extend(getattr(source, "warnings", ()))

    if changelog_available and issues:
        without_history = [i.key for i in issues if not histories.get(i.key)]
        if without_history:
            warnings.append(
                f"{len(without_history)} issue(s) have no recorded status transition; "
                "their status age is measured from the creation date instead."
            )

    issues = enrich_status_since(issues, histories)

    # -- checks -----------------------------------------------------------------
    progress("Running checks", f"{len(active_checks)} check(s)")
    findings: list[Finding] = []
    for check in active_checks:
        if check.requires_changelog and not changelog_available:
            skipped.append(check.id)
            logger.info("skipping %s: status history unavailable", check.id)
            continue
        findings.extend(check.evaluate(issues, ctx))

    if skipped:
        warnings.append(
            "Checks skipped because they need status history: " + ", ".join(sorted(skipped)) + "."
        )
    if config.excluded_statuses:
        warnings.append(
            "Statuses excluded from the current-status-age check: "
            + ", ".join(sorted(config.excluded_statuses))
            + "."
        )

    findings.sort(key=lambda f: (f.sort_key, f.issue_key, f.check_id))

    duration = time.perf_counter() - clock_start
    progress("Complete", f"{len(findings)} finding(s)")

    return ScanResult(
        scan_id=make_scan_id(config.project_key, started_at, config.timezone),
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=duration),
        config=config,
        issues=issues,
        findings=findings,
        warnings=warnings,
        skipped_checks=sorted(skipped),
        source_name=getattr(source, "name", "unknown"),
        duration_seconds=duration,
    )


def findings_by_check(result: ScanResult) -> dict[str, int]:
    """Finding counts keyed by check id, including zero for checks that found nothing."""
    counts = {c.id: 0 for c in default_checks() if c.id not in result.skipped_checks}
    for finding in result.findings:
        counts[finding.check_id] = counts.get(finding.check_id, 0) + 1
    return counts


__all__ = [
    "PERMISSION_CAVEAT",
    "ProgressCallback",
    "ScanProgress",
    "enrich_status_since",
    "findings_by_check",
    "latest_status_change",
    "make_scan_id",
    "run_scan",
]
