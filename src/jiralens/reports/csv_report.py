"""CSV export — one row per finding."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from jiralens.exceptions import ReportGenerationError
from jiralens.models import ScanResult
from jiralens.reports.rows import CSV_COLUMNS, build_rows


def build_dataframe(result: ScanResult) -> pd.DataFrame:
    """Findings as a DataFrame with the published column order.

    An empty scan still produces a correctly shaped frame, so a consumer reading the
    header never has to special-case "no findings".
    """
    rows = build_rows(result, escape=True)
    frame = pd.DataFrame(rows, columns=list(CSV_COLUMNS))
    return frame


def write_csv(result: ScanResult, path: Path) -> Path:
    """Write the findings CSV. Returns the path written.

    Raises:
        ReportGenerationError: if the file cannot be written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        build_dataframe(result).to_csv(path, index=False, lineterminator="\n")
    except OSError as exc:
        raise ReportGenerationError(f"Could not write the CSV report to {path}: {exc}") from exc
    return path


__all__ = ["build_dataframe", "write_csv"]
