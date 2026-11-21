"""The live Jira issue source.

Implements the same ``IssueSource`` protocol the demo source does, so ``run_scan``
is identical for both and the dashboard needed no change to gain live mode.
"""

from __future__ import annotations

from collections.abc import Sequence

from jiralens.exceptions import ProjectNotFoundError
from jiralens.jira.client import JiraClient
from jiralens.jira.normalizer import ISSUE_FIELDS, normalize_issue, normalize_status_changes
from jiralens.logging_config import get_logger
from jiralens.models import IssueRecord, ScanConfig, StatusChange

logger = get_logger("jiralens.sources.jira")


class JiraIssueSource:
    """Serves issues and status histories from a live Jira Cloud site."""

    name = "jira"

    def __init__(self, client: JiraClient, *, validate_project: bool = True) -> None:
        self._client = client
        self._validate_project = validate_project
        #: Issue id to key, captured during fetch_issues. The changelog endpoint keys
        #: its response by id, so this is how histories get matched back to issues.
        self._id_to_key: dict[str, str] = {}
        #: Anything the scan should disclose. ``run_scan`` folds these into
        #: ``ScanResult.warnings`` so a parsing mismatch cannot pass for "no history".
        self.warnings: list[str] = []

    def fetch_issues(self, config: ScanConfig) -> list[IssueRecord]:
        if self._validate_project and config.jql is None:
            # Validate before searching so a mistyped key fails in one fast call with
            # a clear message, rather than as an empty and apparently healthy result.
            project = self._client.get_project(config.project_key)
            logger.info("scanning project %s (%s)", project.key, project.name)

        raw = self._client.search_issues(config.effective_jql(), ISSUE_FIELDS)
        issues = [normalize_issue(item, self._client.site_url) for item in raw]
        self._id_to_key = {issue.id: issue.key for issue in issues}

        if not issues and config.jql is None and not self._validate_project:
            raise ProjectNotFoundError(
                f"No issues were returned for {config.project_key}. Check the project "
                "key and that the account can browse it."
            )
        return issues

    def fetch_status_history(self, issue_keys: Sequence[str]) -> dict[str, list[StatusChange]]:
        """Return transitions per issue **key**, translating from the id-keyed response."""
        wanted = set(issue_keys)
        ids = [issue_id for issue_id, key in self._id_to_key.items() if key in wanted]
        if not ids:
            return {}

        by_id = self._client.get_status_changelogs(ids)
        problems: list[str] = []
        unmatched = 0
        out: dict[str, list[StatusChange]] = {}
        for issue_id, entries in by_id.items():
            key = self._id_to_key.get(issue_id)
            if key is None:
                unmatched += 1
                # A history for an issue the search did not return. Ignore it rather
                # than inventing an issue the audit never saw.
                continue
            changes = normalize_status_changes(entries, problems=problems)
            if changes:
                out[key] = changes

        self._report(by_id, out, problems, unmatched)
        return out

    def _report(
        self,
        by_id: dict[str, list[dict[str, object]]],
        out: dict[str, list[StatusChange]],
        problems: list[str],
        unmatched: int,
    ) -> None:
        """Disclose anything that would otherwise pass for "these issues never moved".

        A silent parse failure is indistinguishable from an untouched backlog in the
        output, which is precisely how a 100% failure went unnoticed until the first
        live run. Jira handing back histories and the engine seeing none is now stated.
        """
        if problems:
            for problem in problems[:5]:
                logger.warning("%s", problem)
            self.warnings.append(
                f"{len(problems)} changelog entr"
                f"{'y' if len(problems) == 1 else 'ies'} could not be read, so the "
                "affected issues fall back to their creation date for status age."
            )

        if by_id and not out:
            self.warnings.append(
                f"Jira returned changelog records for {len(by_id)} issue(s) but none "
                "yielded a usable status transition. Treat every current-status age "
                "in this scan as measured from the creation date, not a real "
                "transition — this usually means the changelog response shape has "
                "changed."
            )

        if unmatched:
            self.warnings.append(
                f"{unmatched} changelog record(s) referred to issues the search did "
                "not return, and were ignored."
            )


__all__ = ["JiraIssueSource"]
