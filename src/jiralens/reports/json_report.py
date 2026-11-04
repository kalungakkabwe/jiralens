"""JSON export — the machine-readable summary.

This is a contract other tools consume, so it carries a ``schema_version``. Bump it
when a field changes meaning or disappears; adding a field is backwards compatible
and does not need a bump.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jiralens.exceptions import ReportGenerationError
from jiralens.models import ScanResult
from jiralens.scanner import findings_by_check

SCHEMA_VERSION = "1.0"


def build_summary(result: ScanResult, *, include_issues: bool = False) -> dict[str, Any]:
    """The JSON payload.

    Args:
        include_issues: also embed every scanned issue. Off by default — the point of
            this file is the findings and the scan's provenance; the full issue list
            belongs in the CSV or the dashboard.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "scan_id": result.scan_id,
        "project": result.config.project_key,
        "generated_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "duration_seconds": round(result.duration_seconds, 3),
        "source": result.source_name,
        "read_only": True,
        "issue_count": result.issue_count,
        "finding_count": result.finding_count,
        "flagged_issue_count": len(result.flagged_issue_keys),
        "clean_issue_count": result.clean_issue_count,
        "counts": result.counts,
        "counts_by_check": findings_by_check(result),
        "skipped_checks": result.skipped_checks,
        "configuration": {
            "jql": result.config.effective_jql(),
            "stale_days": result.config.stale_days,
            "high_priority_stale_days": result.config.high_priority_stale_days,
            "status_age_days": result.config.status_age_days,
            "high_priorities": sorted(result.config.high_priorities),
            "excluded_statuses": sorted(result.config.excluded_statuses),
            "timezone": result.config.timezone,
            "include_changelog": result.config.include_changelog,
        },
        "warnings": result.warnings,
        "findings": [f.model_dump(mode="json") for f in result.findings],
        **(
            {"issues": [i.model_dump(mode="json") for i in result.issues]} if include_issues else {}
        ),
    }


def write_json(result: ScanResult, path: Path, *, include_issues: bool = False) -> Path:
    """Write the JSON summary. Returns the path written.

    Raises:
        ReportGenerationError: if the file cannot be written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = build_summary(result, include_issues=include_issues)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ReportGenerationError(f"Could not write the JSON report to {path}: {exc}") from exc
    return path


__all__ = ["SCHEMA_VERSION", "build_summary", "write_json"]
