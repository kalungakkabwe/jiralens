"""Streamlit app, driven through Streamlit's own AppTest framework.

These assert on behaviour an administrator would notice: the app boots, a demo scan
populates the tables, filters narrow them, and every failure path renders a readable
message instead of a traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"
LIVE_SITE = "https://sandbox.atlassian.net"


def _live_issue(n: int) -> dict[str, Any]:
    """A minimal well-formed Jira payload for the live-mode tests."""
    return {
        "id": str(10000 + n),
        "key": f"ITS-{n}",
        "fields": {
            "summary": f"Issue {n}",
            "issuetype": {"name": "Task"},
            "project": {"key": "ITS"},
            "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
            "assignee": None,
            "priority": {"name": "Medium"},
            "created": "2026-08-01T08:00:00.000+0200",
            "updated": "2026-09-01T08:00:00.000+0200",
            "duedate": None,
            "resolution": None,
            "resolutiondate": None,
        },
    }


CREDENTIAL_ENV = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRALENS_DEMO_MODE")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No developer .env and no inherited credentials leak into these runs."""
    monkeypatch.chdir(tmp_path)
    for name in CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)


def _app() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=120)


@pytest.fixture
def scanned() -> AppTest:
    """An app with a completed demo scan in session state."""
    at = _app()
    at.run()
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    return at


class TestStreamlitMagicSafety:
    """Guard against a whole class of Streamlit-specific bug.

    Streamlit's "magic" wraps every bare expression statement that is not a call in
    ``st.write``. A conditional expression used as a statement — ``st.a() if x else
    st.b()`` — therefore hands a DeltaGenerator to ``st.write``, which falls through
    to ``st.help`` and crashes on source introspection. It reads as perfectly ordinary
    Python, which is exactly why it needs a test rather than care.
    """

    def test_the_app_has_no_bare_non_call_expression_statements(self) -> None:
        import ast

        tree = ast.parse(APP.read_text(encoding="utf-8"))
        offenders = [
            ast.unparse(node.value)[:80]
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Call | ast.Constant)
        ]
        assert offenders == [], f"Streamlit magic would try to render: {offenders}"


class TestBoot:
    def test_the_app_loads_without_a_scan(self) -> None:
        at = _app()
        at.run()
        assert not at.exception, at.exception

    def test_it_explains_what_to_do_first(self) -> None:
        at = _app()
        at.run()
        assert any("Run scan" in str(m.value) for m in at.markdown)

    def test_it_says_no_scan_has_run_yet(self) -> None:
        at = _app()
        at.run()
        assert any("No scan has been run" in str(i.value) for i in at.info)

    def test_demo_is_the_default_mode(self) -> None:
        at = _app()
        at.run()
        assert "Demo" in at.radio(key="mode").value

    def test_it_states_that_the_tool_never_writes(self) -> None:
        at = _app()
        at.run()
        assert any("never writes to Jira" in str(c.value) for c in at.caption)


class TestDemoScan:
    def test_running_a_scan_raises_nothing(self, scanned: AppTest) -> None:
        assert not scanned.exception

    def test_the_overview_metrics_are_populated(self, scanned: AppTest) -> None:
        labels = {m.label: m.value for m in scanned.metric}
        assert labels["Issues scanned"] == "60"
        assert int(labels["High findings"]) > 0
        assert int(labels["Issues with no findings"]) > 0

    def test_the_findings_table_has_rows(self, scanned: AppTest) -> None:
        assert len(scanned.dataframe[0].value) > 0

    def test_the_issue_explorer_shows_every_scanned_issue(self, scanned: AppTest) -> None:
        assert len(scanned.dataframe[1].value) == 60

    def test_the_explorer_includes_issues_with_no_findings(self, scanned: AppTest) -> None:
        explorer = scanned.dataframe[1].value
        assert (explorer["Findings"] == 0).any()

    def test_all_three_downloads_are_offered(self, scanned: AppTest) -> None:
        keys = {b.key for b in scanned.download_button}
        assert keys == {"dl_csv", "dl_html", "dl_json"}

    def test_the_scope_caveat_is_shown(self, scanned: AppTest) -> None:
        assert any("permitted to browse" in str(c.value) for c in scanned.caption)

    def test_the_methodology_tab_describes_every_check(self, scanned: AppTest) -> None:
        text = " ".join(str(m.value) for m in scanned.markdown)
        for name in (
            "Unassigned open work",
            "Overdue unresolved work",
            "Stale open work",
            "High-priority stale work",
            "Excessive current-status age",
        ):
            assert name in text, name

    def test_the_methodology_explains_why_there_is_no_health_score(self, scanned: AppTest) -> None:
        text = " ".join(str(m.value) for m in scanned.markdown)
        assert "no health score" in text.lower()
        assert "72/100" in text


class TestFilters:
    def test_the_severity_filter_narrows_the_table(self, scanned: AppTest) -> None:
        before = len(scanned.dataframe[0].value)
        scanned.multiselect(key="f_sev").set_value(["high"]).run()
        after = scanned.dataframe[0].value
        assert 0 < len(after) < before
        assert set(after["Severity"]) == {"high"}

    def test_filtering_does_not_re_run_the_scan(self, scanned: AppTest) -> None:
        """The scan id must be unchanged, proving the result came from session state."""
        before = next(s.value for s in scanned.subheader)
        scanned.multiselect(key="f_sev").set_value(["high"]).run()
        assert next(s.value for s in scanned.subheader) == before

    def test_the_assignee_filter_narrows_the_table(self, scanned: AppTest) -> None:
        scanned.multiselect(key="f_assignee").set_value(["Unassigned"]).run()
        assert set(scanned.dataframe[0].value["Assignee"]) == {"Unassigned"}

    def test_the_search_box_narrows_the_table(self, scanned: AppTest) -> None:
        scanned.text_input(key="f_search").set_value("DEMO-11").run()
        shown = scanned.dataframe[0].value
        assert len(shown) > 0
        assert set(shown["Issue"]) == {"DEMO-11"}

    def test_a_filter_matching_nothing_renders_an_empty_table_not_an_error(
        self, scanned: AppTest
    ) -> None:
        scanned.text_input(key="f_search").set_value("zzzz-no-such-issue").run()
        assert not scanned.exception
        assert len(scanned.dataframe[0].value) == 0

    def test_the_explorer_can_show_only_clean_issues(self, scanned: AppTest) -> None:
        scanned.checkbox(key="only_clean").set_value(True).run()
        explorer = scanned.dataframe[1].value
        assert len(explorer) > 0
        assert (explorer["Findings"] == 0).all()


class TestThresholdsReachTheEngine:
    def test_a_tighter_stale_threshold_produces_more_findings(self) -> None:
        at = _app()
        at.run()
        at.number_input(key="stale_days").set_value(200)
        at.button(key="run").click().run()
        loose = len(at.dataframe[0].value)

        at.number_input(key="stale_days").set_value(5)
        at.button(key="run").click().run()
        tight = len(at.dataframe[0].value)

        assert tight > loose

    def test_excluding_a_status_reduces_status_age_findings(self) -> None:
        at = _app()
        at.run()
        at.button(key="run").click().run()
        before = len(at.dataframe[0].value)

        at.multiselect(key="excluded").set_value(["To Do"])
        at.button(key="run").click().run()
        assert len(at.dataframe[0].value) < before


class TestHonestSkipping:
    def test_skipping_history_warns_rather_than_reporting_zero(self) -> None:
        at = _app()
        at.run()
        at.checkbox(key="no_changelog").set_value(True)
        at.button(key="run").click().run()
        assert not at.exception
        warnings = " ".join(str(w.value) for w in at.warning)
        assert "status_age" in warnings
        assert "not a finding of zero" in warnings

    def test_the_methodology_marks_the_skipped_check(self) -> None:
        at = _app()
        at.run()
        at.checkbox(key="no_changelog").set_value(True)
        at.button(key="run").click().run()
        text = " ".join(str(m.value) for m in at.markdown)
        assert "not evaluated in this scan" in text


class TestLiveMode:
    def test_live_mode_without_credentials_names_the_missing_variables(self) -> None:
        at = _app()
        at.run()
        at.radio(key="mode").set_value("Live Jira")
        at.button(key="run").click().run()
        assert not at.exception, "a missing credential must not surface as a traceback"
        errors = " ".join(str(e.value) for e in at.error)
        assert "JIRA_BASE_URL" in errors

    @staticmethod
    def _credentials(monkeypatch: pytest.MonkeyPatch, token: str = "token-value-123456") -> None:
        monkeypatch.setenv("JIRA_BASE_URL", LIVE_SITE)
        monkeypatch.setenv("JIRA_EMAIL", "a@b.com")
        monkeypatch.setenv("JIRA_API_TOKEN", token)

    @respx.mock
    def test_live_mode_runs_a_scan_through_the_same_engine_as_demo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The source seam is the only difference between demo and live."""
        self._credentials(monkeypatch)
        respx.get(f"{LIVE_SITE}/rest/api/3/project/ITS").mock(
            return_value=httpx.Response(200, json={"id": "1", "key": "ITS", "name": "IT"})
        )
        respx.post(f"{LIVE_SITE}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": [_live_issue(1), _live_issue(2)]})
        )
        respx.post(f"{LIVE_SITE}/rest/api/3/changelog/bulkfetch").mock(
            return_value=httpx.Response(200, json={"issueChangeLogs": []})
        )

        at = _app()
        at.run()
        # The project input only exists in live mode, so rerun before reaching for it.
        at.radio(key="mode").set_value("Live Jira").run()
        at.text_input(key="project").set_value("ITS")
        at.button(key="run").click().run()

        assert not at.exception, at.exception
        labels = {m.label: m.value for m in at.metric}
        assert labels["Issues scanned"] == "2"
        assert len(at.dataframe[1].value) == 2

    @respx.mock
    def test_an_unreachable_site_renders_a_message_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._credentials(monkeypatch)
        respx.get(f"{LIVE_SITE}/rest/api/3/project/ITS").mock(
            side_effect=httpx.ConnectError("no route to host")
        )
        at = _app()
        at.run()
        # The project input only exists in live mode, so rerun before reaching for it.
        at.radio(key="mode").set_value("Live Jira").run()
        at.text_input(key="project").set_value("ITS")
        at.button(key="run").click().run()

        assert not at.exception
        errors = " ".join(str(e.value) for e in at.error)
        assert "could not reach Jira" in errors

    @respx.mock
    def test_rejected_credentials_are_explained_in_plain_language(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._credentials(monkeypatch)
        respx.get(f"{LIVE_SITE}/rest/api/3/project/ITS").mock(return_value=httpx.Response(401))
        at = _app()
        at.run()
        # The project input only exists in live mode, so rerun before reaching for it.
        at.radio(key="mode").set_value("Live Jira").run()
        at.text_input(key="project").set_value("ITS")
        at.button(key="run").click().run()

        assert not at.exception
        errors = " ".join(str(e.value) for e in at.error)
        assert "rejected the supplied credentials" in errors

    def test_no_credential_is_ever_rendered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = "ATATT-super-secret-value-0987654321"
        monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@b.com")
        monkeypatch.setenv("JIRA_API_TOKEN", token)
        at = _app()
        at.run()
        at.radio(key="mode").set_value("Live Jira")
        at.button(key="run").click().run()
        rendered = " ".join(
            str(e.value) for group in (at.error, at.markdown, at.caption) for e in group
        )
        assert token not in rendered
