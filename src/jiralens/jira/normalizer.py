"""Convert raw Jira JSON into ``IssueRecord``.

Two notes that matter at build time:

* ``POST /rest/api/3/search/jql`` returns only ``id`` and ``key`` unless ``fields``
  is passed explicitly. ``ISSUE_FIELDS`` is that list, and it is deliberately narrow:
  no descriptions, comments, attachments, worklogs or unrestricted custom fields.
* ``status.statusCategory.key`` is the only safe way to tell open from done. The
  category *name* is localised and site-configurable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, get_args

from jiralens.exceptions import JiraApiError
from jiralens.models import IssueRecord, StatusCategory, StatusChange

#: The only fields JiraLens requests from Jira.
ISSUE_FIELDS: tuple[str, ...] = (
    "summary",
    "issuetype",
    "project",
    "status",
    "assignee",
    "priority",
    "created",
    "updated",
    "duedate",
    "resolution",
    "resolutiondate",
)

_VALID_CATEGORIES: frozenset[str] = frozenset(get_args(StatusCategory))


def _parse_datetime(value: Any, *, field: str, key: str) -> datetime:
    """Parse a Jira timestamp such as ``2026-06-01T08:00:00.000-0500``.

    ``datetime.fromisoformat`` handles both ``-0500`` and ``-05:00`` offsets and
    millisecond precision on Python 3.11+, so no custom format string is needed.
    """
    if not isinstance(value, str) or not value:
        raise JiraApiError(f"{key}: field {field!r} is missing a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise JiraApiError(f"{key}: could not parse {field!r} value {value!r}") from exc
    if parsed.tzinfo is None:
        raise JiraApiError(f"{key}: timestamp {field!r} has no UTC offset")
    return parsed


def _parse_optional_datetime(value: Any, *, field: str, key: str) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_datetime(value, field=field, key=key)


def _parse_optional_date(value: Any, *, field: str, key: str) -> date | None:
    """Jira's ``duedate`` is a bare calendar date with no timezone."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise JiraApiError(f"{key}: field {field!r} is not a date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise JiraApiError(f"{key}: could not parse {field!r} value {value!r}") from exc


def _nested_name(container: Any) -> str | None:
    """Pull ``.name`` from a nullable Jira object (priority, resolution, issuetype)."""
    if not isinstance(container, Mapping):
        return None
    name = container.get("name")
    return name if isinstance(name, str) and name else None


def normalize_issue(raw: Mapping[str, Any], base_url: str) -> IssueRecord:
    """Build an ``IssueRecord`` from one Jira issue payload.

    Raises:
        JiraApiError: if a field JiraLens cannot work without is absent or malformed.
    """
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        raise JiraApiError("Jira returned an issue with no key")

    issue_id = raw.get("id")
    if not isinstance(issue_id, str) or not issue_id:
        raise JiraApiError(f"{key}: issue has no id")

    fields = raw.get("fields")
    if not isinstance(fields, Mapping):
        raise JiraApiError(f"{key}: issue payload has no 'fields' object (was 'fields' requested?)")

    status = fields.get("status")
    if not isinstance(status, Mapping):
        raise JiraApiError(f"{key}: issue has no status")
    status_name = _nested_name(status) or "Unknown"

    category = status.get("statusCategory")
    category_key = category.get("key") if isinstance(category, Mapping) else None
    if category_key not in _VALID_CATEGORIES:
        raise JiraApiError(
            f"{key}: unexpected statusCategory key {category_key!r}; "
            f"expected one of {sorted(_VALID_CATEGORIES)}"
        )

    assignee = fields.get("assignee")
    assignee_id: str | None = None
    assignee_name: str | None = None
    if isinstance(assignee, Mapping):
        raw_id = assignee.get("accountId")
        raw_name = assignee.get("displayName")
        assignee_id = raw_id if isinstance(raw_id, str) and raw_id else None
        assignee_name = raw_name if isinstance(raw_name, str) and raw_name else None

    summary = fields.get("summary")

    return IssueRecord(
        id=issue_id,
        key=key,
        url=f"{base_url.rstrip('/')}/browse/{key}",
        summary=summary if isinstance(summary, str) else "",
        issue_type=_nested_name(fields.get("issuetype")) or "Unknown",
        status=status_name,
        status_category=category_key,  # type: ignore[arg-type]
        priority=_nested_name(fields.get("priority")),
        assignee_account_id=assignee_id,
        assignee_name=assignee_name,
        created_at=_parse_datetime(fields.get("created"), field="created", key=key),
        updated_at=_parse_datetime(fields.get("updated"), field="updated", key=key),
        due_date=_parse_optional_date(fields.get("duedate"), field="duedate", key=key),
        resolution=_nested_name(fields.get("resolution")),
        resolution_date=_parse_optional_datetime(
            fields.get("resolutiondate"), field="resolutiondate", key=key
        ),
        current_status_since=None,  # enriched from the changelog in Phase 5
    )


def normalize_issues(raws: Iterable[Mapping[str, Any]], base_url: str) -> list[IssueRecord]:
    return [normalize_issue(raw, base_url) for raw in raws]


def parse_changelog_timestamp(value: Any) -> datetime | None:
    """Parse a changelog ``created`` value, or return None if it cannot be read.

    Atlassian is not consistent here, and the difference is invisible until you call
    the real API: ``POST /rest/api/3/changelog/bulkfetch`` returns **epoch
    milliseconds** (``1795256864259``), while the issue ``changelog`` expand returns an
    ISO-8601 string. Accepting only the documented string form silently discarded
    every live history.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():  # epoch milliseconds that arrived as a string
            return datetime.fromtimestamp(int(text) / 1000, tz=UTC)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        # A naive timestamp is refused rather than assumed to be UTC: guessing wrong
        # shifts every status age by the site's offset.
        return parsed if parsed.tzinfo is not None else None
    return None


def normalize_status_changes(
    entries: Sequence[Mapping[str, Any]],
    *,
    problems: list[str] | None = None,
) -> list[StatusChange]:
    """Extract status transitions from changelog entries, oldest first.

    Each entry carries an ``items`` array; only status items are kept. An entry that
    cannot be read is skipped rather than failing the scan — one malformed history
    should not abort an audit of 500 issues — but every skip is recorded in
    ``problems`` when a sink is given. Skipping silently is how a 100% parse failure
    once looked exactly like "these issues never transitioned".
    """
    changes: list[StatusChange] = []

    def note(message: str) -> None:
        if problems is not None:
            problems.append(message)

    for entry in entries:
        entry_id = entry.get("id", "?")
        changed_at = parse_changelog_timestamp(entry.get("created"))
        if changed_at is None:
            note(f"changelog entry {entry_id}: unreadable 'created' value")
            continue
        items = entry.get("items")
        if not isinstance(items, Sequence) or isinstance(items, str | bytes):
            note(f"changelog entry {entry_id}: 'items' is missing or not a list")
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            if (item.get("field") or item.get("fieldId")) not in ("status", "Status"):
                continue
            changes.append(
                StatusChange(
                    changed_at=changed_at,
                    from_status=item.get("fromString") or None,
                    to_status=item.get("toString") or None,
                )
            )
    changes.sort(key=lambda c: c.changed_at)
    return changes


__all__ = [
    "ISSUE_FIELDS",
    "parse_changelog_timestamp",
    "normalize_issue",
    "normalize_issues",
    "normalize_status_changes",
]
