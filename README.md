# JiraLens

A read-only Jira Cloud project health auditor for Jira administrators.

[![CI](https://github.com/kalungakkabwe/jiralens/actions/workflows/ci.yml/badge.svg)](https://github.com/kalungakkabwe/jiralens/actions/workflows/ci.yml)

## The problem

Answering "what is actually at risk in this project?" normally means running five or
six JQL queries, pasting the results into a spreadsheet and reading them by hand —
every time, for every project. JiraLens runs those checks against a project and
returns a dashboard, a findings table and exportable reports.

It never writes to Jira. There is no create, update, transition, comment or delete
path in the codebase, and a test enforces that.

## What it checks

| Check | Rule | Default severity |
|---|---|---|
| Unassigned open work | Not Done, no assignee | Warning |
| Overdue unresolved work | Due date before today, not Done | High |
| Stale open work | No update for more than *n* days | Warning |
| High-priority stale work | Priority is high and no recent update | High |
| Excessive status age | In the same working status for more than *n* days | Warning / High |

Thresholds, high-priority names and excluded statuses are all configurable, because
Jira sites rename these freely.

## Build status

This repository is being built in phases. Phases 0 to 3 are complete, so the full
audit engine runs end to end against synthetic data today:

- **Phase 0** — packaging, validated configuration, exception hierarchy, logging with
  credential redaction, CI.
- **Phase 1** — typed domain models with an injected clock, the Jira issue normaliser,
  the `IssueSource` protocol, and a synthetic dataset of 61 issues covering every
  audit condition plus the known boundary cases.
- **Phase 2** — the five checks behind an `AuditCheck` protocol with a central registry.
- **Phase 3** — `run_scan` orchestration returning a complete `ScanResult`, with
  progress stages and honest handling of missing status history.
- **Phase 4** — CSV, JSON and HTML exporters, all driven from one `ScanResult`.
- **Phases 6–8** — the read-only Jira client and the Streamlit dashboard. See
  `CHANGELOG.md`.

```
$ jiralens demo --exclude-status "To Do" --output reports/demo
Scan 20261104-DEMO-103000
  Issues scanned             60
  Findings                   60   (18 high, 42 warning)
  Issues with findings       47
  Issues with no findings    13

Reports written
  csv   reports/demo/20261104-DEMO-103000-findings.csv
  json  reports/demo/20261104-DEMO-103000-summary.json
  html  reports/demo/20261104-DEMO-103000-report.html
```

## Sample output

`sample_reports/` contains real output from the synthetic dataset — open
[`example-report.html`](sample_reports/example-report.html) to see the executive
report, or the CSV and JSON beside it. They are generated at a fixed instant by
`scripts/generate_sample_reports.py`, so regeneration is byte-identical and a diff
always means a deliberate change.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
jiralens version
jiralens demo          # runs against synthetic data, no credentials needed
```

## Configuration

```bash
cp .env.example .env   # then edit .env locally — it is gitignored
```

`JIRA_API_TOKEN` is created at
[id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens).
The token is never accepted as a command-line argument, because shell history
retains it, and it is held as a `SecretStr` so it cannot surface through a `repr`,
a traceback or a logged model dump.

## Required Jira permissions

Browse Projects on each audited project. Nothing else. Findings reflect only the
issues the authenticated account is permitted to see, which every report states
explicitly.

## Design notes

Two decisions shape the codebase:

**The clock is injected.** No check ever calls `datetime.now()`. The current time
arrives as `ScanContext.now`, which makes every check a deterministic pure function
and means no test depends on the wall clock.

**`IssueSource` is a protocol.** `run_scan` depends on the protocol, not on the Jira
client, so demo mode runs the real audit engine rather than a parallel fake — and the
whole pipeline is testable with no HTTP mocking.

Status is read from `statusCategory.key` (`new` / `indeterminate` / `done`), never
from the category display name, which is localised and site-configurable.

## Known limitations

- Findings depend on the authenticated account's browse permissions.
- Calendar days, not business days or SLA calendars.
- Jira's `updated` field moves for automation and administrative events, so the stale
  check is an inactivity indicator, not proof that nobody is working on an issue.
- `POST /rest/api/3/search/jql` returns no total and paginates by token only, so the
  issue count is known once pagination completes, not before.
- No health score. A single number would depend on arbitrary weights.

## Roadmap

JSM SLA analysis · permission comparison · custom-field hygiene · sprint health ·
configuration drift.

## Licence

MIT. See `LICENSE`.
