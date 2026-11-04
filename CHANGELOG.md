# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Phase 0** — packaging, validated configuration, exception hierarchy, logging with
  credential redaction, CI workflow.
- **Phase 1** — typed domain models with an injected clock, Jira issue normaliser keyed
  on `statusCategory.key`, `IssueSource` protocol, and a synthetic dataset of 61
  fixtures covering every audit condition plus the known boundary cases.
- **Phase 2** — the five audit checks behind an `AuditCheck` protocol, with a central
  registry so later checks need no change to the orchestrator:
  - `unassigned` — open work with no assignee
  - `overdue` — open work past its due date (due *today* is not overdue)
  - `stale` — no recorded activity beyond the stale threshold
  - `high_priority_stale` — configurable priority names, tighter threshold
  - `status_age` — days in the current status, from the latest changelog transition
- **Phase 3** — `run_scan` orchestration: the single entry point every interface calls.
  Validates config, fetches issues through `IssueSource`, retrieves status histories,
  enriches `current_status_since`, runs the registered checks, sorts findings and
  returns a `ScanResult`. Emits `ScanProgress` stages so an interface can show progress
  without holding any audit logic.
- `jiralens demo` now runs a real end-to-end audit of the synthetic dataset and prints
  the overview, per-check counts, top findings and scope caveats. The renderer is shared
  with the live `scan` command arriving in Phase 7.
- **Phase 4** — the three exporters, all driven from one `ScanResult` so they cannot
  disagree with each other or with the dashboard:
  - CSV — one row per finding, 20 published columns, findings joined back to their
    issue for assignee, status and dates
  - JSON — versioned machine-readable summary (`schema_version`), counts, the full
    configuration so a scan is reproducible, warnings, skipped checks and findings
  - HTML — executive report with summary cards, high and warning tables, grouping by
    check and by assignee, methodology, limitations and scan metadata
- `jiralens demo --output DIR --format csv|json|html` writes them; files are named
  `<scan_id>-*` so repeated scans accumulate instead of overwriting.
- `sample_reports/` holds committed example output, generated at a fixed instant by
  `scripts/generate_sample_reports.py` so regeneration is byte-identical.

- **Phase 8** — the Streamlit dashboard, with four tabs: Overview (metrics, findings
  by check, scope caveats, downloads for all three report formats), Findings (filter
  by severity, check, assignee, priority, status and minimum age, plus free-text
  search), Issue explorer (every scanned issue including the clean ones) and
  Methodology (what each check means, the thresholds used, limitations, and why there
  is no health score).
- Table shaping and filtering live in `jiralens.dashboard`, not in `streamlit_app.py`,
  so they are unit-tested without starting Streamlit. The app collects input, calls
  `run_scan` and draws the result.
- The completed `ScanResult` is held in session state, so changing a filter re-renders
  from memory and never re-runs the scan or calls Jira again.
- A source seam (`build_source`) means Phase 7 plugs `JiraIssueSource` in without any
  other change to the app. Live mode today explains itself rather than failing.
- Streamlit AppTest coverage: the app boots, a demo scan populates every table,
  filters narrow results, thresholds reach the engine, skipping history surfaces the
  skipped check, and every failure path renders a message rather than a traceback.

### Export safety

- **CSV formula injection**: any text cell beginning `=`, `+`, `-`, `@`, tab or
  carriage return is prefixed with an apostrophe, so a Jira summary cannot execute
  when an administrator opens the export in Excel. Numeric cells are deliberately left
  alone — escaping them would turn a negative value into text and break every total.
- **HTML injection**: Jinja2 autoescaping is set explicitly to `True`, because
  `select_autoescape` does not enable escaping for a `.html.j2` filename. A summary
  containing markup renders as text; the fixtures include such a payload and a test
  asserts the escaped form appears and the raw form does not.
- The HTML report embeds no scripts and loads nothing from the network, so it opens
  correctly from a local folder, an email attachment or a printer.
- `StrictUndefined` is set, so a typo in the template fails the render instead of
  silently producing a blank cell in a report someone will act on.

### Decisions pinned by tests

- Thresholds are **exclusive**: at 30 days, an issue last updated exactly 30 calendar
  days ago is not stale; 31 days is.
- An issue due today is not overdue, and "today" comes from the configured display
  timezone rather than the server clock.
- `status_age` falls back to the creation timestamp when an issue has never
  transitioned, so a freshly created issue is never flagged.
- No check flags a Done issue, under any combination of inputs.
- No check mutates the issues it is given, and repeated runs are identical.
- `status_age` sets `requires_changelog`, so `--no-changelog` records it in
  `ScanResult.skipped_checks` rather than reporting zero findings — and
  `findings_by_check` omits a skipped check entirely rather than showing it as `0`.
- A failed changelog fetch degrades to a warning plus a skipped check instead of
  aborting the scan: a partial audit that names what is missing beats no audit.
- The latest status transition is found by timestamp, never by list position, because
  a paginated history is exactly where that assumption fails silently.
- Every scan states the permission caveat, so no report implies more coverage than the
  authenticated account actually has.

### Not yet implemented

- Phase 6/7 live Jira client (`connection-test` and `scan` validate configuration and
  then stop), Phase 8 Streamlit dashboard.
