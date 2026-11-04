"""Properties that must hold for every check, now and for every check added later.

These are the tests that stop a sixth check from quietly breaking the contract.
"""

from __future__ import annotations

import pytest

from jiralens.checks import DEFAULT_CHECKS, default_checks
from jiralens.checks.base import AuditCheck
from jiralens.models import ScanConfig, ScanContext
from jiralens.sources.demo import DemoIssueSource
from tests.conftest import FROZEN_NOW
from tests.factories import make_issue

EXPECTED_IDS = {"unassigned", "overdue", "stale", "high_priority_stale", "status_age"}


@pytest.fixture
def checks() -> list[AuditCheck]:
    return default_checks()


class TestRegistry:
    def test_all_five_mvp_checks_are_registered(self, checks: list[AuditCheck]) -> None:
        assert {c.id for c in checks} == EXPECTED_IDS

    def test_ids_are_unique(self, checks: list[AuditCheck]) -> None:
        ids = [c.id for c in checks]
        assert len(ids) == len(set(ids))

    def test_the_registry_returns_a_fresh_list_each_call(self) -> None:
        """A caller must not be able to mutate the shared check set."""
        first = default_checks()
        first.clear()
        assert len(default_checks()) == 5

    def test_the_constant_matches_the_factory(self) -> None:
        assert {c.id for c in DEFAULT_CHECKS} == {c.id for c in default_checks()}

    def test_only_the_status_age_check_needs_the_changelog(self, checks: list[AuditCheck]) -> None:
        needs = {c.id for c in checks if c.requires_changelog}
        assert needs == {"status_age"}

    def test_the_base_class_refuses_to_be_used_as_a_check(self, ctx: ScanContext) -> None:
        """A subclass that forgets to implement evaluate must fail loudly."""
        from jiralens.checks.base import BaseCheck

        with pytest.raises(NotImplementedError):
            BaseCheck().evaluate([make_issue()], ctx)


@pytest.mark.parametrize("check", default_checks(), ids=lambda c: c.id)
class TestEveryCheckHonoursTheContract:
    def test_it_satisfies_the_protocol(self, check: AuditCheck) -> None:
        assert isinstance(check, AuditCheck)

    def test_it_has_a_stable_id_and_a_human_name(self, check: AuditCheck) -> None:
        assert check.id and check.id.islower() and " " not in check.id
        assert check.name and check.name[0].isupper()

    def test_it_documents_itself_for_the_methodology_page(self, check: AuditCheck) -> None:
        assert len(check.description) > 30
        assert check.__doc__ is not None

    def test_empty_input_yields_no_findings(self, check: AuditCheck, ctx: ScanContext) -> None:
        assert check.evaluate([], ctx) == []

    def test_it_never_flags_a_done_issue(self, check: AuditCheck, ctx: ScanContext) -> None:
        """Every rule is scoped to open work. No exceptions."""
        issue = make_issue(
            status="Done",
            assignee=None,
            priority="Highest",
            created_days_ago=400,
            updated_days_ago=400,
            due_days_from_now=-300,
            status_since_days_ago=400,
        )
        assert check.evaluate([issue], ctx) == []

    def test_it_does_not_mutate_the_issues_it_is_given(
        self, check: AuditCheck, ctx: ScanContext
    ) -> None:
        issues = [
            make_issue(
                "DEMO-1",
                assignee=None,
                priority="Highest",
                created_days_ago=120,
                updated_days_ago=60,
                due_days_from_now=-30,
                status_since_days_ago=40,
            )
        ]
        before = [i.model_dump() for i in issues]
        check.evaluate(issues, ctx)
        assert [i.model_dump() for i in issues] == before

    def test_every_finding_carries_the_checks_own_identity(
        self, check: AuditCheck, ctx: ScanContext
    ) -> None:
        issue = make_issue(
            assignee=None,
            priority="Highest",
            created_days_ago=120,
            updated_days_ago=60,
            due_days_from_now=-30,
            status_since_days_ago=40,
        )
        for finding in check.evaluate([issue], ctx):
            assert finding.check_id == check.id
            assert finding.check_name == check.name
            assert finding.issue_key == issue.key
            assert finding.issue_url == issue.url
            assert finding.reason
            assert finding.recommendation

    def test_it_is_deterministic_across_repeated_runs(
        self, check: AuditCheck, ctx: ScanContext
    ) -> None:
        issues = [
            make_issue(f"DEMO-{n}", assignee=None, updated_days_ago=n * 10, status_since_days_ago=n)
            for n in range(1, 8)
        ]
        first = check.evaluate(issues, ctx)
        second = check.evaluate(issues, ctx)
        assert [f.model_dump() for f in first] == [f.model_dump() for f in second]


class TestAgainstTheSyntheticDataset:
    """The full check set against the bundled fixtures — the Phase 3 smoke test."""

    @pytest.fixture
    def scanned(self, config: ScanConfig) -> tuple[list, ScanContext]:
        source = DemoIssueSource(now=FROZEN_NOW)
        issues = source.fetch_issues(config)
        histories = source.fetch_status_history([i.key for i in issues])
        enriched = [
            issue.with_status_since(
                histories[issue.key][-1].changed_at if histories.get(issue.key) else None
            )
            for issue in issues
        ]
        return enriched, ScanContext(now=FROZEN_NOW, config=config)

    def test_every_check_finds_something(self, scanned: tuple[list, ScanContext]) -> None:
        issues, ctx = scanned
        for check in default_checks():
            assert check.evaluate(issues, ctx), f"{check.id} found nothing in the demo data"

    def test_no_finding_references_a_closed_issue(self, scanned: tuple[list, ScanContext]) -> None:
        issues, ctx = scanned
        open_keys = {i.key for i in issues if i.is_open}
        for check in default_checks():
            for finding in check.evaluate(issues, ctx):
                assert finding.issue_key in open_keys

    def test_some_issue_triggers_more_than_one_check(
        self, scanned: tuple[list, ScanContext]
    ) -> None:
        """The dataset must exercise the multi-finding path."""
        issues, ctx = scanned
        counts: dict[str, int] = {}
        for check in default_checks():
            for finding in check.evaluate(issues, ctx):
                counts[finding.issue_key] = counts.get(finding.issue_key, 0) + 1
        assert max(counts.values()) >= 3

    def test_the_boundary_fixtures_behave_as_labelled(
        self, scanned: tuple[list, ScanContext]
    ) -> None:
        issues, ctx = scanned
        flagged: dict[str, set[str]] = {}
        for check in default_checks():
            for finding in check.evaluate(issues, ctx):
                flagged.setdefault(finding.issue_key, set()).add(check.id)

        assert "overdue" not in flagged.get("DEMO-52", set()), "due today must not be overdue"
        assert "stale" not in flagged.get("DEMO-53", set()), "30 days is at the threshold"
        assert "stale" not in flagged.get("DEMO-54", set()), "29 days is under the threshold"
        assert "stale" in flagged.get("DEMO-55", set()), "31 days is over the threshold"
        assert "status_age" not in flagged.get("DEMO-56", set()), "created 3 days ago"
        assert "status_age" in flagged.get("DEMO-57", set()), "created 60 days ago, never moved"
        assert "DEMO-51" not in flagged, "the Done issue must never appear"
