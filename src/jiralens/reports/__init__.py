"""Report writers.

Every exporter takes a finished ``ScanResult`` and writes one file. None of them
re-derive a finding or re-read Jira, so the three formats cannot disagree with each
other or with the dashboard.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from jiralens.exceptions import ReportGenerationError
from jiralens.models import ScanResult
from jiralens.reports.csv_report import build_dataframe, write_csv
from jiralens.reports.html_report import render_html, write_html
from jiralens.reports.json_report import SCHEMA_VERSION, build_summary, write_json
from jiralens.reports.rows import CSV_COLUMNS, build_rows, escape_formula, group_by

#: The formats ``write_reports`` understands, in the order it writes them.
ALL_FORMATS: tuple[str, ...] = ("csv", "json", "html")

#: Filename suffix per format, prefixed by the scan id.
_SUFFIX = {"csv": "findings.csv", "json": "summary.json", "html": "report.html"}


def write_reports(
    result: ScanResult,
    output_dir: Path | str,
    *,
    formats: Sequence[str] = ALL_FORMATS,
    include_issues: bool = False,
) -> dict[str, Path]:
    """Write the requested reports into ``output_dir``.

    Files are named ``<scan_id>-<kind>``, so repeated scans of the same project
    accumulate rather than overwrite each other.

    Args:
        result: the completed scan.
        output_dir: created if it does not exist.
        formats: any of ``csv``, ``json``, ``html``.
        include_issues: embed the full issue list in the JSON export.

    Returns:
        A mapping of format name to the path written.

    Raises:
        ReportGenerationError: on an unknown format or any write failure.
    """
    unknown = sorted(set(formats) - set(ALL_FORMATS))
    if unknown:
        raise ReportGenerationError(
            f"Unknown report format(s): {', '.join(unknown)}. Choose from {', '.join(ALL_FORMATS)}."
        )

    directory = Path(output_dir)
    written: dict[str, Path] = {}
    for kind in ALL_FORMATS:
        if kind not in formats:
            continue
        path = directory / f"{result.scan_id}-{_SUFFIX[kind]}"
        if kind == "csv":
            written[kind] = write_csv(result, path)
        elif kind == "json":
            written[kind] = write_json(result, path, include_issues=include_issues)
        else:
            written[kind] = write_html(result, path)
    return written


__all__ = [
    "ALL_FORMATS",
    "CSV_COLUMNS",
    "SCHEMA_VERSION",
    "build_dataframe",
    "build_rows",
    "build_summary",
    "escape_formula",
    "group_by",
    "render_html",
    "write_csv",
    "write_html",
    "write_json",
    "write_reports",
]
