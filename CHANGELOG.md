# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Found by the first live scan

Everything below this heading was invisible to 632 passing tests, because the
fixtures were written from Atlassian's documented examples and the documentation is
wrong or incomplete in each case. Only a call to a real site found them.

- **`changelog/bulkfetch` returns `created` as epoch milliseconds**, while the issue
  `changelog` expand returns ISO-8601. The normaliser accepted only the string form,
  so **100% of live status histories were silently discarded** and every issue fell
  back to its creation date for status age. The output stayed plausible throughout: an
  issue transitioned minutes earlier reported "remained in 'In Progress' for 15
  calendar days since it was created". Fixed; regression tests use a verbatim live
  payload rather than the documented example.
- **Silent skips are now recorded.** A dropped changelog entry and an issue that never
  moved produced identical output, which is what let a total parse failure pass for an
  untouched backlog. Skips name the entry and reason, the source states plainly when
  Jira returned records but none yielded a transition, and `run_scan` folds source
  warnings into `ScanResult.warnings` so a source-level problem cannot stay invisible.
- **Service account tokens** (`ATST` prefix) authenticate with `Authorization: Bearer`,
  not `email:token` basic auth, and must use the `api.atlassian.com` gateway. Sent as
  basic auth they return 401, which reads exactly like a mistyped credential. The
  scheme is now resolved from the token, `JIRA_EMAIL` is not required under bearer, and
  an `ATST` token without `JIRA_CLOUD_ID` is refused up front rather than failing three
  calls later.
- **`.env` problems that produce misleading errors** are reported before any request:
  a line with no `=` (pasting over a placeholder usually deletes the `KEY=` with it,
  so the value never reaches the tool), a placeholder left in place, and any
  environment variable shadowing the file — environment beats `.env`, so a stale
  `export` makes edits inert while the error points at the file you just corrected.
- `connection-test` showed `Site: Jira`, Atlassian's generic `serverTitle`, which
  identifies nothing. It now shows the site URL, plus the token type and auth scheme.

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

- **Phases 6 and 7** — the read-only Jira Cloud client and the live issue source.
  `jiralens connection-test` and `jiralens scan` now work against a real site, and the
  dashboard's live mode runs the same engine as demo mode.
- **Scoped API token support.** Atlassian now offers scoped tokens alongside classic
  unscoped ones, and they are called through `api.atlassian.com/ex/jira/{cloudId}`
  rather than the site URL. Set `JIRA_CLOUD_ID` to use one; leave it blank for a
  classic token. Scoping a token to `read:jira-work` means the credential itself
  cannot write, which is a stronger guarantee than an endpoint allowlist alone.
- The site URL and the API base URL are now separate values. Issue links in every
  report are built from the site URL — building them from the gateway would produce a
  dead link for every finding.

### Read-only enforcement

- Every request passes through one `_request` method that refuses any verb outside
  GET/POST and any path not matching `READ_ONLY_ENDPOINTS`, raising
  `ReadOnlyViolationError`. The allowlist holds exactly five operations and a test
  asserts that count, so widening the surface is a deliberate, reviewable change.
- No `create_issue`, `update_issue`, `transition_issue`, `add_comment` or
  `delete_issue` method exists, and a test asserts none of those names is on the
  client's public surface.
- A test runs a complete scan with a catch-all route registered for PUT, PATCH and
  DELETE, and asserts it was never called. That is the evidence behind the read-only
  claim.

### Resilience

- Retry with exponential backoff and jitter on 429, 500, 502, 503, 504 and timeouts;
  400, 401, 403 and 404 are permanent answers and are never retried.
- `Retry-After` is honoured verbatim when Jira sends a numeric value, as Atlassian
  asks. An HTTP-date value falls back to local backoff rather than parsing dates
  against a possibly skewed clock.
- Token pagination carries a repeated-token guard: a server returning the same token
  twice raises rather than looping forever and accumulating duplicates.
- Changelogs are batched at the endpoint's 1,000-id limit, each batch is followed
  through its own pagination, and pages are merged rather than replaced — taking only
  the last page would silently lose the transition that determines status age.
- The changelog response is keyed by issue **id**; the source translates back to keys.
  Getting that mapping wrong would attach one issue's history to another and produce
  status ages that are wrong but entirely plausible.

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

- **Test-data seeder.** `scripts/seed_test_project.py` populates a scratch project
  with ~150 varied issues — weighted due dates (past, today, future, none), mixed
  priorities, roughly half unassigned, and genuine transitions on a subset — so the
  checks that have never flagged anything against live data finally face some. It is
  the only file here that writes to Jira, lives outside the package, and
  `tests/test_seed_isolation.py` asserts it never imports the read-only client and
  that no PUT, PATCH or DELETE literal appears inside `jiralens`. Two seeded
  summaries are adversarial by design: a spreadsheet formula and an HTML payload,
  which exercise the CSV and HTML escaping against data Jira actually returned.
  Jira will not let any API backdate `updated`, so `stale` can only be exercised
  here with a zero threshold — the code path, not the aged-data behaviour.

- **Documentation and repository bootstrap.** `docs/architecture.md` carries the
  layer diagram, a live scan end to end, the read-only chokepoint and the three
  authentication paths. `scripts/bootstrap_github.sh` creates the v0.1.0 milestone,
  the label set and the sixteen-issue backlog in one idempotent run.

### Remaining before v0.1.0

- Branch ruleset, GitHub issue backlog, and the release itself.
