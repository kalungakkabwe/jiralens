"""Synthetic issue source.

The fixtures are stored in Jira's own response shape and pass through the *real*
normaliser, so demo mode exercises the same code path as a live scan. Date fields
hold relative tokens (``@-46d``) that are rebased against ``now`` at load time, which
keeps the dataset meaningful in any future year.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from importlib import resources
from typing import Any

from jiralens.exceptions import JiraApiError
from jiralens.jira.normalizer import normalize_issue, normalize_status_changes
from jiralens.models import IssueRecord, ScanConfig, StatusChange

DEMO_BASE_URL = "https://demo-site.example.net"
DEMO_PROJECT_KEY = "DEMO"

#: ``@-46d`` is 46 days before now, ``@+3d`` is 3 days after, ``@0d`` is now.
_TOKEN = re.compile(r"^@([+-]?)(\d+)d$")

#: Fields Jira returns as a bare calendar date rather than a timestamp.
_DATE_ONLY_FIELDS = frozenset({"duedate"})


def _rebase(token: str, now: datetime, *, date_only: bool) -> str:
    match = _TOKEN.match(token)
    if not match:
        raise JiraApiError(f"Malformed demo date token {token!r}; expected e.g. '@-30d'")
    sign, days = match.group(1), int(match.group(2))
    delta = timedelta(days=-days if sign == "-" else days)
    moment = now + delta
    if date_only:
        return moment.date().isoformat()
    # Jira renders milliseconds and a +HHMM offset; mirror that exactly.
    return (
        moment.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{moment.microsecond // 1000:03d}"
        + moment.strftime("%z")
    )


def _walk(value: Any, now: datetime, *, field: str | None = None) -> Any:
    if isinstance(value, str) and value.startswith("@"):
        return _rebase(value, now, date_only=field in _DATE_ONLY_FIELDS)
    if isinstance(value, dict):
        return {k: _walk(v, now, field=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, now, field=field) for v in value]
    return value


def _load(filename: str) -> Any:
    with resources.files("jiralens.data").joinpath(filename).open(encoding="utf-8") as handle:
        return json.load(handle)


class DemoIssueSource:
    """Serves the bundled synthetic dataset. Requires no credentials and no network."""

    name = "demo"

    def __init__(self, now: datetime, base_url: str = DEMO_BASE_URL) -> None:
        if now.tzinfo is None:
            raise ValueError("DemoIssueSource requires a timezone-aware 'now'")
        self._now = now
        self._base_url = base_url

    def fetch_issues(self, config: ScanConfig) -> list[IssueRecord]:
        raw = _load("demo_issues.json")
        issues = raw["issues"] if isinstance(raw, dict) else raw
        rebased = _walk(issues, self._now)
        records = [normalize_issue(item, self._base_url) for item in rebased]
        # Mirror the live default JQL: the scan is scoped to open work unless the
        # caller supplied custom JQL, in which case demo mode returns everything.
        if config.jql is None:
            records = [r for r in records if r.is_open]
        return records

    def fetch_status_history(self, issue_keys: Sequence[str]) -> dict[str, list[StatusChange]]:
        raw = _load("demo_changelogs.json")
        histories = raw["issueChangeLogs"] if isinstance(raw, dict) else raw
        rebased = _walk(histories, self._now)
        wanted = set(issue_keys)
        out: dict[str, list[StatusChange]] = {}
        for entry in rebased:
            key = entry.get("issueKey")
            if key not in wanted:
                continue
            out[key] = normalize_status_changes(entry.get("changeHistories", []))
        return out


__all__ = ["DEMO_BASE_URL", "DEMO_PROJECT_KEY", "DemoIssueSource"]
