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

- Phase 4 reports, Phase 5 CLI exports, Phase 6/7 live Jira client, Phase 8 Streamlit
  dashboard.
