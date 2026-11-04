"""Normalisation. One test per nullable field, per the Phase 4 exit criteria."""

from __future__ import annotations

import copy
from datetime import UTC, date
from typing import Any

import pytest

from jiralens.exceptions import JiraApiError
from jiralens.jira.normalizer import (
    ISSUE_FIELDS,
    normalize_issue,
    normalize_issues,
    normalize_status_changes,
)
from tests.conftest import BASE_URL


def _without(raw: dict[str, Any], field: str) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    out["fields"][field] = None
    return out


class TestHappyPath:
    def test_core_fields_are_mapped(self, raw_issue: dict[str, Any]) -> None:
        issue = normalize_issue(raw_issue, BASE_URL)
        assert issue.id == "10001"
        assert issue.key == "DEMO-1"
        assert issue.summary == "Renew customer reporting certificate"
        assert issue.issue_type == "Task"
        assert issue.status == "In Progress"
        assert issue.status_category == "indeterminate"
        assert issue.priority == "High"
        assert issue.assignee_account_id == "acc-123"
        assert issue.assignee_name == "K. Mwale"
        assert issue.due_date == date(2026, 7, 15)

    def test_url_is_built_from_the_base_url(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(raw_issue, BASE_URL).url == f"{BASE_URL}/browse/DEMO-1"

    def test_trailing_slash_on_base_url_does_not_double(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(raw_issue, BASE_URL + "/").url == f"{BASE_URL}/browse/DEMO-1"

    def test_current_status_since_starts_unset(self, raw_issue: dict[str, Any]) -> None:
        """It is enriched from the changelog later, never guessed here."""
        assert normalize_issue(raw_issue, BASE_URL).current_status_since is None

    def test_normalize_issues_preserves_order(self, raw_issue: dict[str, Any]) -> None:
        second = copy.deepcopy(raw_issue)
        second["key"] = "DEMO-2"
        second["id"] = "10002"
        assert [i.key for i in normalize_issues([raw_issue, second], BASE_URL)] == [
            "DEMO-1",
            "DEMO-2",
        ]


class TestTimestampParsing:
    def test_jira_offset_without_a_colon_is_parsed(self, raw_issue: dict[str, Any]) -> None:
        """Jira sends -0500, not -05:00."""
        issue = normalize_issue(raw_issue, BASE_URL)
        assert issue.created_at.utcoffset() is not None
        assert issue.created_at.astimezone(UTC).hour == 13  # 08:00 -0500

    def test_offset_with_a_colon_is_also_parsed(self, raw_issue: dict[str, Any]) -> None:
        raw_issue["fields"]["created"] = "2026-06-01T08:00:00.000-05:00"
        assert normalize_issue(raw_issue, BASE_URL).created_at.utcoffset() is not None

    def test_utc_z_suffix_is_parsed(self, raw_issue: dict[str, Any]) -> None:
        raw_issue["fields"]["created"] = "2026-06-01T08:00:00.000Z"
        assert normalize_issue(raw_issue, BASE_URL).created_at.utcoffset() is not None

    def test_naive_timestamp_is_rejected_rather_than_assumed(
        self, raw_issue: dict[str, Any]
    ) -> None:
        raw_issue["fields"]["created"] = "2026-06-01T08:00:00.000"
        with pytest.raises(JiraApiError, match="no UTC offset"):
            normalize_issue(raw_issue, BASE_URL)

    def test_unparseable_timestamp_names_the_field_and_the_issue(
        self, raw_issue: dict[str, Any]
    ) -> None:
        raw_issue["fields"]["updated"] = "last Tuesday"
        with pytest.raises(JiraApiError, match="DEMO-1.*'updated'"):
            normalize_issue(raw_issue, BASE_URL)


class TestNullableFields:
    def test_missing_assignee(self, raw_issue: dict[str, Any]) -> None:
        issue = normalize_issue(_without(raw_issue, "assignee"), BASE_URL)
        assert issue.assignee_account_id is None
        assert issue.assignee_name is None
        assert issue.assignee_display == "Unassigned"

    def test_assignee_present_but_without_a_display_name(self, raw_issue: dict[str, Any]) -> None:
        raw_issue["fields"]["assignee"] = {"accountId": "acc-9"}
        issue = normalize_issue(raw_issue, BASE_URL)
        assert issue.assignee_account_id == "acc-9"
        assert issue.assignee_name is None

    def test_missing_priority(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(_without(raw_issue, "priority"), BASE_URL).priority is None

    def test_missing_due_date(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(_without(raw_issue, "duedate"), BASE_URL).due_date is None

    def test_empty_string_due_date(self, raw_issue: dict[str, Any]) -> None:
        raw_issue["fields"]["duedate"] = ""
        assert normalize_issue(raw_issue, BASE_URL).due_date is None

    def test_malformed_due_date_is_an_error(self, raw_issue: dict[str, Any]) -> None:
        raw_issue["fields"]["duedate"] = "15/07/2026"
        with pytest.raises(JiraApiError, match="duedate"):
            normalize_issue(raw_issue, BASE_URL)

    def test_missing_resolution(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(_without(raw_issue, "resolution"), BASE_URL).resolution is None

    def test_missing_resolution_date(self, raw_issue: dict[str, Any]) -> None:
        issue = normalize_issue(_without(raw_issue, "resolutiondate"), BASE_URL)
        assert issue.resolution_date is None

    def test_resolved_issue_carries_both(self, raw_issue: dict[str, Any]) -> None:
        raw_issue["fields"]["status"] = {
            "name": "Done",
            "statusCategory": {"key": "done", "name": "Done"},
        }
        raw_issue["fields"]["resolution"] = {"name": "Fixed"}
        raw_issue["fields"]["resolutiondate"] = "2026-07-02T10:00:00.000-0500"
        issue = normalize_issue(raw_issue, BASE_URL)
        assert issue.resolution == "Fixed"
        assert issue.resolution_date is not None
        assert issue.is_open is False

    def test_missing_summary_becomes_empty_string(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(_without(raw_issue, "summary"), BASE_URL).summary == ""

    def test_missing_issue_type_becomes_unknown(self, raw_issue: dict[str, Any]) -> None:
        assert normalize_issue(_without(raw_issue, "issuetype"), BASE_URL).issue_type == "Unknown"


class TestStatusCategory:
    @pytest.mark.parametrize("key", ["new", "indeterminate", "done"])
    def test_valid_keys_are_accepted(self, raw_issue: dict[str, Any], key: str) -> None:
        raw_issue["fields"]["status"]["statusCategory"]["key"] = key
        assert normalize_issue(raw_issue, BASE_URL).status_category == key

    def test_localised_category_name_is_rejected_loudly(self, raw_issue: dict[str, Any]) -> None:
        """'En cours' must fail fast rather than silently mark an issue done."""
        raw_issue["fields"]["status"]["statusCategory"] = {"key": "En cours", "name": "En cours"}
        with pytest.raises(JiraApiError, match="unexpected statusCategory"):
            normalize_issue(raw_issue, BASE_URL)

    def test_absent_category_is_rejected(self, raw_issue: dict[str, Any]) -> None:
        del raw_issue["fields"]["status"]["statusCategory"]
        with pytest.raises(JiraApiError, match="unexpected statusCategory"):
            normalize_issue(raw_issue, BASE_URL)


class TestMalformedPayloads:
    def test_missing_key(self, raw_issue: dict[str, Any]) -> None:
        del raw_issue["key"]
        with pytest.raises(JiraApiError, match="no key"):
            normalize_issue(raw_issue, BASE_URL)

    def test_missing_id(self, raw_issue: dict[str, Any]) -> None:
        del raw_issue["id"]
        with pytest.raises(JiraApiError, match="no id"):
            normalize_issue(raw_issue, BASE_URL)

    def test_missing_fields_object_hints_at_the_real_cause(self, raw_issue: dict[str, Any]) -> None:
        """search/jql returns only id and key unless fields is passed explicitly."""
        del raw_issue["fields"]
        with pytest.raises(JiraApiError, match="was 'fields' requested"):
            normalize_issue(raw_issue, BASE_URL)

    def test_missing_status(self, raw_issue: dict[str, Any]) -> None:
        del raw_issue["fields"]["status"]
        with pytest.raises(JiraApiError, match="no status"):
            normalize_issue(raw_issue, BASE_URL)


class TestRequestedFields:
    def test_sensitive_fields_are_never_requested(self) -> None:
        for forbidden in ("description", "comment", "attachment", "worklog", "reporter"):
            assert forbidden not in ISSUE_FIELDS

    def test_the_fields_the_checks_need_are_all_requested(self) -> None:
        for required in ("status", "assignee", "priority", "created", "updated", "duedate"):
            assert required in ISSUE_FIELDS


class TestStatusChangeExtraction:
    def _entry(self, created: str, frm: str, to: str, field: str = "status") -> dict[str, Any]:
        return {
            "id": "1",
            "created": created,
            "items": [{"field": field, "fieldId": field, "fromString": frm, "toString": to}],
        }

    def test_empty_history(self) -> None:
        assert normalize_status_changes([]) == []

    def test_single_change(self) -> None:
        changes = normalize_status_changes(
            [self._entry("2026-10-01T09:00:00.000+0200", "To Do", "In Progress")]
        )
        assert len(changes) == 1
        assert changes[0].from_status == "To Do"
        assert changes[0].to_status == "In Progress"

    def test_changes_are_sorted_oldest_first_even_if_jira_returns_them_reversed(self) -> None:
        """This is the pagination trap: never trust the incoming order."""
        changes = normalize_status_changes(
            [
                self._entry("2026-10-20T09:00:00.000+0200", "In Review", "In Progress"),
                self._entry("2026-10-01T09:00:00.000+0200", "To Do", "In Progress"),
                self._entry("2026-10-10T09:00:00.000+0200", "In Progress", "In Review"),
            ]
        )
        assert [c.changed_at.day for c in changes] == [1, 10, 20]
        assert changes[-1].to_status == "In Progress"

    def test_non_status_items_are_ignored(self) -> None:
        entries = [
            self._entry("2026-10-01T09:00:00.000+0200", "Low", "High", field="priority"),
            self._entry("2026-10-02T09:00:00.000+0200", "To Do", "In Progress"),
        ]
        changes = normalize_status_changes(entries)
        assert len(changes) == 1
        assert changes[0].to_status == "In Progress"

    def test_entry_with_an_unparseable_timestamp_is_skipped_not_fatal(self) -> None:
        """One bad history must not abort an audit of hundreds of issues."""
        entries = [
            self._entry("not a date", "To Do", "In Progress"),
            self._entry("2026-10-02T09:00:00.000+0200", "To Do", "In Progress"),
        ]
        assert len(normalize_status_changes(entries)) == 1

    def test_entry_with_no_items_is_skipped(self) -> None:
        assert (
            normalize_status_changes([{"id": "1", "created": "2026-10-02T09:00:00.000+0200"}]) == []
        )
