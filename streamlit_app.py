"""Streamlit entry point. Phase 8.

This file stays thin on purpose: it collects input, calls the scan service and
renders the returned ScanResult. No audit logic belongs here.
"""

from __future__ import annotations

import streamlit as st

from jiralens import __version__

st.set_page_config(page_title="JiraLens", page_icon="🔎", layout="wide")

st.title("JiraLens")
st.caption(f"Read-only Jira Cloud project health auditor · v{__version__}")

st.info(
    "The dashboard is Phase 8. Phases 0 and 1 are in place: configuration, domain "
    "models with an injected clock, the Jira normaliser, and the synthetic demo source.",
    icon="🚧",
)

with st.sidebar:
    st.header("Scan")
    st.radio("Mode", ["Demo", "Live Jira"], disabled=True, horizontal=True)
    st.text_input("Project key", value="DEMO", disabled=True)
    st.number_input("Stale threshold (days)", value=30, disabled=True)
    st.number_input("High-priority stale (days)", value=7, disabled=True)
    st.number_input("Status age (days)", value=14, disabled=True)
    st.button("Run scan", disabled=True)

st.subheader("Planned pages")
st.markdown(
    "- **Overview** — issues scanned, findings by severity, issues with no findings\n"
    "- **Findings** — filterable table with links back to Jira\n"
    "- **Issue explorer** — every scanned issue, so input completeness is verifiable\n"
    "- **Methodology** — what each check means, thresholds, and the known limitations"
)
