"""JiraLens dashboard.

This file collects input, calls ``run_scan`` and draws the returned ``ScanResult``.
It contains no audit logic and no filtering logic — the checks live in
``jiralens.checks``, orchestration in ``jiralens.scanner``, table shaping and
filtering in ``jiralens.dashboard``. That separation is what makes the engine
testable and this file replaceable.

Run locally with::

    streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import available_timezones

import streamlit as st

from jiralens import __version__
from jiralens.checks import default_checks
from jiralens.config import Settings, load_settings
from jiralens.dashboard import (
    FilterSpec,
    apply_filters,
    check_counts_frame,
    filter_options,
    findings_dataframe,
    issues_dataframe,
    max_age,
)
from jiralens.exceptions import JiraLensError
from jiralens.jira.client import JiraClient
from jiralens.models import ScanConfig, ScanResult
from jiralens.reports.csv_report import build_dataframe
from jiralens.reports.html_report import LIMITATIONS, METHODOLOGY_NOTES, render_html
from jiralens.reports.json_report import build_summary
from jiralens.scanner import ScanProgress, run_scan
from jiralens.sources import IssueSource
from jiralens.sources.demo import DEMO_PROJECT_KEY, DemoIssueSource
from jiralens.sources.jira_source import JiraIssueSource

DEMO = "Demo (synthetic data)"
LIVE = "Live Jira"
COMMON_WAITING_STATUSES = ["To Do", "Waiting for Customer", "Waiting on Vendor", "On Hold"]

st.set_page_config(page_title="JiraLens", page_icon="🔎", layout="wide")


# ---------------------------------------------------------------------------
# Source seam. Phase 7 replaces the body of the live branch with JiraIssueSource;
# nothing else in this file changes.
# ---------------------------------------------------------------------------


def build_source(mode: str, settings: Settings, now: datetime) -> IssueSource:
    """Pick the source. The only place in this file that knows live mode exists."""
    if mode == DEMO:
        return DemoIssueSource(now=now)
    settings.require_live()  # raises a readable ConfigurationError when unset
    # The client is kept on session state so its connection pool survives a rerun
    # rather than being rebuilt on every widget interaction.
    client = st.session_state.get("jira_client")
    if client is None:
        client = JiraClient(settings)
        st.session_state["jira_client"] = client
    return JiraIssueSource(client)


def execute_scan(mode: str, config: ScanConfig, settings: Settings) -> ScanResult:
    now = datetime.now(tz=config.tzinfo)
    with st.status("Scanning…", expanded=True) as status:
        lines: list[str] = []

        def show(progress: ScanProgress) -> None:
            lines.append(str(progress))
            status.write(str(progress))

        source = build_source(mode, settings, now)
        result = run_scan(source, config, now=now, on_progress=show)
        status.update(
            label=f"Scanned {result.issue_count} issues · {result.finding_count} findings",
            state="complete",
            expanded=False,
        )
    return result


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

try:
    settings = load_settings()
except JiraLensError as exc:
    st.error(str(exc))
    st.stop()

with st.sidebar:
    st.title("JiraLens")
    st.caption(f"Read-only Jira auditor · v{__version__}")

    # A public deployment holds no credentials, so offering Live mode there would
    # only ever produce a configuration error. JIRALENS_LIVE_JIRA_DISABLED removes
    # the option entirely rather than letting a visitor walk into a dead end.
    live_available = not settings.jiralens_live_jira_disabled
    mode = st.radio(
        "Mode", [DEMO, LIVE] if live_available else [DEMO], key="mode", horizontal=False
    )
    if not live_available:
        st.caption(
            "Live mode is disabled on this deployment. This runs the real audit "
            "engine against a synthetic dataset and holds no credentials for any "
            "Jira site."
        )

    if mode == DEMO:
        project = DEMO_PROJECT_KEY
        st.text_input("Project key", value=DEMO_PROJECT_KEY, disabled=True, key="demo_project")
        jql = None
    else:
        project = st.text_input("Project key", value=settings.jira_default_project, key="project")
        jql = st.text_area("Custom JQL (optional)", value="", key="jql").strip() or None

    st.subheader("Thresholds")
    stale_days = st.number_input("Stale after (days)", 1, 365, 30, key="stale_days")
    hp_stale_days = st.number_input(
        "High-priority stale after (days)", 1, 365, 7, key="hp_stale_days"
    )
    status_age_days = st.number_input(
        "Current-status age (days)", 1, 365, 14, key="status_age_days"
    )
    high_priorities = st.multiselect(
        "Priorities treated as high",
        ["Highest", "High", "Medium", "Critical", "Blocker", "P1", "P2"],
        default=["Highest", "High"],
        key="high_priorities",
        help="Jira sites rename priorities freely, so this is configurable.",
    )

    st.subheader("Exclusions")
    excluded = st.multiselect(
        "Statuses excluded from status age",
        COMMON_WAITING_STATUSES,
        default=[],
        key="excluded",
        help=(
            "Statuses that legitimately sit still. Excluding a backlog status such as "
            "'To Do' shows work stuck in flight rather than work not yet started."
        ),
    )
    include_changelog = not st.checkbox(
        "Skip status history (faster)",
        value=False,
        key="no_changelog",
        help="Skips the current-status-age check entirely rather than reporting zero.",
    )

    zones = sorted(available_timezones())
    default_zone = settings.jiralens_timezone
    timezone = st.selectbox(
        "Display timezone",
        zones,
        index=zones.index(default_zone) if default_zone in zones else 0,
        key="timezone",
    )

    run = st.button("Run scan", type="primary", key="run", use_container_width=True)
    st.caption("JiraLens never writes to Jira.")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if run:
    try:
        config = ScanConfig(
            project_key=project,
            jql=jql,
            stale_days=int(stale_days),
            high_priority_stale_days=int(hp_stale_days),
            status_age_days=int(status_age_days),
            high_priorities=frozenset(high_priorities or {"Highest", "High"}),
            excluded_statuses=frozenset(excluded),
            timezone=timezone,
            include_changelog=include_changelog,
        )
        st.session_state["result"] = execute_scan(mode, config, settings)
    except JiraLensError as exc:
        # A readable message, never a traceback: the audience is an administrator.
        st.error(str(exc))
    except ValueError as exc:
        st.error(f"That configuration is not valid: {exc}")

result: ScanResult | None = st.session_state.get("result")

if result is None:
    st.title("JiraLens")
    st.write(
        "A read-only Jira Cloud project health auditor. Choose a mode in the sidebar "
        "and press **Run scan**. Demo mode needs no credentials and runs the same "
        "audit engine as a live scan."
    )
    cols = st.columns(len(default_checks()))
    for col, check in zip(cols, default_checks(), strict=True):
        with col:
            st.markdown(f"**{check.name}**")
            st.caption(check.description)
    st.info("No scan has been run in this session yet.", icon="ℹ️")
    st.stop()

# The scan result is held in session state, so changing a filter below re-renders
# from memory and never re-runs the scan or calls Jira again.
config = result.config
findings = findings_dataframe(result)
issues = issues_dataframe(result)

overview_tab, findings_tab, explorer_tab, method_tab = st.tabs(
    ["Overview", "Findings", "Issue explorer", "Methodology"]
)

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

with overview_tab:
    st.subheader(f"{config.project_key} · {result.scan_id}")
    st.caption(
        f"{result.issue_count} issues scanned from the {result.source_name} source in "
        f"{result.duration_seconds:.2f}s · display timezone {config.timezone}"
    )

    counts = result.counts
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Issues scanned", result.issue_count)
    c2.metric("High findings", counts["high"])
    c3.metric("Warnings", counts["warning"])
    c4.metric("Issues flagged", len(result.flagged_issue_keys))
    c5.metric("Issues with no findings", result.clean_issue_count)

    st.markdown("##### Findings by check")
    # Note: never write a bare conditional expression as a statement in a Streamlit
    # script. Streamlit's "magic" auto-renders any non-call expression statement via
    # st.write, which here would pass a DeltaGenerator to st.help and crash.
    check_counts = check_counts_frame(result)
    if check_counts.empty:
        st.caption("No findings.")
    else:
        st.bar_chart(check_counts, x="Check", y="Findings", horizontal=True, height=220)

    for skipped in result.skipped_checks:
        st.warning(
            f"**{skipped}** was not evaluated in this scan, so this report makes no "
            "statement about it. That is not a finding of zero.",
            icon="⚠️",
        )

    st.markdown("##### Scope and limitations")
    for warning in result.warnings:
        st.caption(f"• {warning}")

    st.markdown("##### Downloads")
    d1, d2, d3 = st.columns(3)
    d1.download_button(
        "Findings CSV",
        build_dataframe(result).to_csv(index=False, lineterminator="\n"),
        file_name=f"{result.scan_id}-findings.csv",
        mime="text/csv",
        key="dl_csv",
        use_container_width=True,
    )
    d2.download_button(
        "Executive HTML",
        render_html(result),
        file_name=f"{result.scan_id}-report.html",
        mime="text/html",
        key="dl_html",
        use_container_width=True,
    )
    d3.download_button(
        "Summary JSON",
        json.dumps(build_summary(result), indent=2),
        file_name=f"{result.scan_id}-summary.json",
        mime="application/json",
        key="dl_json",
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

with findings_tab:
    if findings.empty:
        st.success("No findings. Every scanned issue passed all evaluated checks.")
    else:
        options = filter_options(findings)
        f1, f2, f3 = st.columns(3)
        with f1:
            sev = st.multiselect("Severity", options["Severity"], key="f_sev")
            checks_chosen = st.multiselect("Check", options["Check"], key="f_check")
        with f2:
            assignees = st.multiselect("Assignee", options["Assignee"], key="f_assignee")
            priorities = st.multiselect("Priority", options["Priority"], key="f_priority")
        with f3:
            statuses = st.multiselect("Status", options["Status"], key="f_status")
            ceiling = max_age(findings)
            min_age = st.slider("Minimum age (days)", 0, ceiling, 0, key="f_age") if ceiling else 0
        search = st.text_input(
            "Search issue key, summary or reason", key="f_search", placeholder="VPN"
        )

        spec = FilterSpec(
            severities=sev,
            checks=checks_chosen,
            assignees=assignees,
            priorities=priorities,
            statuses=statuses,
            min_age_days=int(min_age),
            search=search,
        )
        shown = apply_filters(findings, spec)
        st.caption(
            f"Showing {len(shown)} of {len(findings)} findings"
            + ("" if spec.is_empty else " (filtered)")
        )
        st.dataframe(
            shown,
            hide_index=True,
            use_container_width=True,
            column_config={
                "URL": st.column_config.LinkColumn("Open", display_text="Jira ↗"),
                "Summary": st.column_config.TextColumn(width="medium"),
                "Reason": st.column_config.TextColumn(width="large"),
                "Recommendation": st.column_config.TextColumn(width="large"),
            },
        )

# ---------------------------------------------------------------------------
# Issue explorer
# ---------------------------------------------------------------------------

with explorer_tab:
    st.caption(
        "Every issue the scan retrieved, including those with no findings. This table "
        "is how you confirm the scan saw the whole project rather than a subset."
    )
    only_clean = st.checkbox("Show only issues with no findings", key="only_clean")
    shown_issues = issues[issues["Findings"] == 0] if only_clean else issues
    st.caption(f"Showing {len(shown_issues)} of {len(issues)} issues")
    st.dataframe(
        shown_issues,
        hide_index=True,
        use_container_width=True,
        column_config={
            "URL": st.column_config.LinkColumn("Open", display_text="Jira ↗"),
            "Summary": st.column_config.TextColumn(width="large"),
        },
    )

# ---------------------------------------------------------------------------
# Methodology
# ---------------------------------------------------------------------------

with method_tab:
    st.markdown("#### What each check means")
    for check in default_checks():
        skipped = check.id in result.skipped_checks
        st.markdown(f"**{check.name}**" + (" — _not evaluated in this scan_" if skipped else ""))
        st.caption(check.description)

    st.markdown("#### Thresholds used in this scan")
    st.markdown(
        f"- Stale: **{config.stale_days}** calendar days\n"
        f"- High-priority stale: **{config.high_priority_stale_days}** calendar days\n"
        f"- Current-status age: **{config.status_age_days}** calendar days\n"
        f"- Priorities treated as high: **{', '.join(sorted(config.high_priorities))}**\n"
        f"- Statuses excluded from status age: "
        f"**{', '.join(sorted(config.excluded_statuses)) or 'none'}**\n"
        f"- Display timezone: **{config.timezone}**\n"
        f"- Query: `{config.effective_jql()}`"
    )

    st.markdown("#### How ages are counted")
    for note in METHODOLOGY_NOTES:
        st.markdown(f"- {note}")

    st.markdown("#### Limitations")
    for item in LIMITATIONS:
        st.markdown(f"- {item}")

    st.markdown("#### Why there is no health score")
    st.markdown(
        "A single number such as 72/100 would read as more precise than the data "
        "supports, because it would depend entirely on weights chosen arbitrarily. "
        "The findings and their severities are reported instead."
    )

    with st.expander("Scan metadata"):
        st.json(
            {
                "scan_id": result.scan_id,
                "started_at": result.started_at.isoformat(),
                "completed_at": result.completed_at.isoformat(),
                "duration_seconds": round(result.duration_seconds, 3),
                "source": result.source_name,
                "issue_count": result.issue_count,
                "finding_count": result.finding_count,
                "skipped_checks": result.skipped_checks,
                "read_only": True,
            }
        )
