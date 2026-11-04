#!/usr/bin/env python3
"""Generate the committed sample reports.

Run from the repo root:

    python scripts/generate_sample_reports.py

These files live in the public repository so a reader can see real output without
installing anything or having Jira credentials. They are built from the synthetic
dataset at a **fixed instant**, so regenerating them is byte-identical and a diff
always means a deliberate change — the same guarantee the fixtures have.

The fixed clock is also why the filenames are stable (``example-*``) rather than
scan-id prefixed: a committed artefact needs a name that does not churn.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jiralens.models import ScanConfig
from jiralens.reports import write_reports
from jiralens.scanner import run_scan
from jiralens.sources.demo import DEMO_PROJECT_KEY, DemoIssueSource

OUT_DIR = Path(__file__).resolve().parents[1] / "sample_reports"

#: Frozen so the output is reproducible. Matches the suite's FROZEN_NOW.
SAMPLE_NOW = datetime(2026, 11, 4, 10, 30, tzinfo=ZoneInfo("Africa/Lusaka"))

#: The backlog status is excluded here so the sample shows work stuck in flight
#: rather than a long-lived backlog. See the report's methodology section.
SAMPLE_CONFIG = ScanConfig(
    project_key=DEMO_PROJECT_KEY,
    excluded_statuses=frozenset({"Waiting for Customer", "To Do"}),
    timezone="Africa/Lusaka",
)

RENAMES = {
    "csv": "example-findings.csv",
    "json": "example-summary.json",
    "html": "example-report.html",
}


def main() -> None:
    result = run_scan(DemoIssueSource(now=SAMPLE_NOW), SAMPLE_CONFIG, now=SAMPLE_NOW)
    # Zero the elapsed time: a real duration would differ on every machine and make
    # the committed files churn without anything meaningful having changed.
    result = result.model_copy(update={"duration_seconds": 0.0, "completed_at": result.started_at})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = write_reports(result, OUT_DIR)
    for kind, path in written.items():
        target = OUT_DIR / RENAMES[kind]
        target.write_bytes(path.read_bytes())
        path.unlink()
        print(f"wrote sample_reports/{target.name}  ({target.stat().st_size:,} bytes)")

    print(
        f"\n{result.issue_count} issues scanned, {result.finding_count} findings "
        f"({result.counts['high']} high, {result.counts['warning']} warning), "
        f"{result.clean_issue_count} clean"
    )


if __name__ == "__main__":
    main()
