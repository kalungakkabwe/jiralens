#!/usr/bin/env bash
#
# One-shot GitHub bootstrap: milestone, labels and the v0.1.0 issue backlog.
#
# Idempotent — re-running it updates existing labels and skips issues whose exact
# title already exists, so a partial failure is safe to retry.
#
# Requires the GitHub CLI, authenticated against an account with write access:
#   brew install gh && gh auth login
#
# Usage:
#   scripts/bootstrap_github.sh                 # against the repo in the cwd
#   REPO=kalungakkabwe/jiralens scripts/bootstrap_github.sh
#   DRY_RUN=1 scripts/bootstrap_github.sh       # print what would happen

set -euo pipefail

MILESTONE="v0.1.0"
DRY_RUN="${DRY_RUN:-}"

command -v gh >/dev/null 2>&1 || {
  echo "error: the GitHub CLI (gh) is not installed. brew install gh" >&2
  exit 1
}
gh auth status >/dev/null 2>&1 || {
  echo "error: gh is not authenticated. Run: gh auth login" >&2
  exit 1
}

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
echo "Repository: $REPO"
[ -n "$DRY_RUN" ] && echo "(dry run — nothing will be created)"

# Dry-run output goes to stderr deliberately. Every call site redirects stdout to
# /dev/null to suppress gh's JSON; printing the preview on stdout would send it
# there too, and a dry run indistinguishable from a live one is worse than none.
run() {
  if [ -n "$DRY_RUN" ]; then
    { printf '  would run:'; printf ' %q' "$@"; printf '\n'; } >&2
  else
    "$@"
  fi
}

if [ -n "$DRY_RUN" ]; then
  DID_CREATE="would create"; DID_UPDATE="would update"; DID_EXIST="exists, would skip"
else
  DID_CREATE="created"; DID_UPDATE="updated"; DID_EXIST="skipped (exists)"
fi

# -- milestone ----------------------------------------------------------------
# No `gh milestone` command exists; the REST API is the supported route.
echo
echo "Milestone"
if gh api "repos/$REPO/milestones?state=all" -q '.[].title' | grep -qx "$MILESTONE"; then
  echo "  $MILESTONE $DID_EXIST"
else
  run gh api "repos/$REPO/milestones" \
    -f title="$MILESTONE" \
    -f state="open" \
    -f description="First tagged release: validated against a real site, CI green, branch protection on." \
    >/dev/null
  echo "  $DID_CREATE $MILESTONE"
fi

# -- labels -------------------------------------------------------------------
echo
echo "Labels"
label() {   # label <name> <colour> <description>
  if gh label list --repo "$REPO" --limit 200 --json name -q '.[].name' | grep -qx "$1"; then
    run gh label edit "$1" --repo "$REPO" --color "$2" --description "$3" >/dev/null
    echo "  $DID_UPDATE $1"
  else
    run gh label create "$1" --repo "$REPO" --color "$2" --description "$3" >/dev/null
    echo "  $DID_CREATE $1"
  fi
}

label "area:checks"     "1d76db" "The five audit checks"
label "area:client"     "1d76db" "Jira HTTP client, pagination, retry"
label "area:reports"    "1d76db" "CSV, JSON and HTML exporters"
label "area:dashboard"  "1d76db" "Streamlit app and table shaping"
label "area:cli"        "1d76db" "Typer command-line interface"
label "area:ci"         "1d76db" "GitHub Actions, tooling, repo settings"
label "area:docs"       "1d76db" "README, architecture, changelog"

label "type:bug"        "d73a4a" "Something produces a wrong or misleading result"
label "type:feature"    "0e8a16" "New capability"
label "type:test"       "fbca04" "Coverage, fixtures, acceptance tests"
label "type:chore"      "ededed" "Maintenance, release mechanics, settings"
label "type:security"   "b60205" "Credential handling, read-only enforcement, injection"

label "priority:high"   "b60205" "Blocks the release"
label "priority:medium" "fbca04" "Wanted for the release"
label "priority:low"    "c2e0c6" "Backlog"

label "unproven-in-production" "5319e7" "Correct in tests; never exercised against real data"

# -- issues -------------------------------------------------------------------
echo
echo "Issues"
existing="$(gh issue list --repo "$REPO" --state all --limit 500 --json title -q '.[].title')"

issue() {   # issue <title> <labels-csv> <milestone-or-empty> <body>
  if printf '%s\n' "$existing" | grep -qxF "$1"; then
    echo "  $DID_EXIST: $1"
    return
  fi
  if [ -n "$3" ]; then
    run gh issue create --repo "$REPO" --title "$1" --label "$2" --milestone "$3" --body "$4" >/dev/null
  else
    run gh issue create --repo "$REPO" --title "$1" --label "$2" --body "$4" >/dev/null
  fi
  echo "  $DID_CREATE: $1"
}

# --- release blockers --------------------------------------------------------

issue "Validate against a project with 100+ issues to exercise token pagination" \
  "area:client,type:test,priority:high,unproven-in-production" "$MILESTONE" \
'`POST /rest/api/3/search/jql` paginates by token and returns no `total`. Every scan
so far has run against JTS (~49 issues), which fits in a single page, so the
pagination loop, the repeated-token guard and page merging have only ever been
exercised by `respx` mocks built from Atlassian'"'"'s documented examples.

The epoch-milliseconds defect proved that documented examples and live responses
differ in ways 632 passing tests cannot see.

**Done when** a scan against a project of at least 100 issues returns an issue count
matching the same JQL run in the Jira UI, and the debug log shows more than one page
fetched.'

issue "Acceptance test: cross-check findings against hand-written JQL" \
  "area:checks,type:test,priority:high" "$MILESTONE" \
'For each check, write the equivalent JQL, run it in the Jira UI, and assert JiraLens
returns the same issue keys.

| Check | Equivalent JQL |
| --- | --- |
| `unassigned` | `project = X AND assignee IS EMPTY AND statusCategory != Done` |
| `overdue` | `project = X AND due < startOfDay() AND statusCategory != Done` |
| `stale` | `project = X AND updated < -30d AND statusCategory != Done` |

`status_age` has no JQL equivalent — that is precisely why it is the check most able
to be wrong without anyone noticing, and why the epoch-ms bug survived as long as it
did. Spot-check it by hand against the issue history of five issues.

**Done when** the comparison is automated in `tests/test_acceptance.py` against a
recorded fixture, and the manual procedure is written up in `docs/`.'

issue "First CI run: push to GitHub and fix what the runner surfaces" \
  "area:ci,type:chore,priority:high" "$MILESTONE" \
'`.github/workflows/ci.yml` has never executed — there was no remote until now.

Expect the usual first-run differences: the runner'"'"'s Python patch version, missing
system locales, timezone data, and anything that passes locally only because of a
cached artefact.

**Done when** the workflow is green on `main`, including the byte-reproducibility
check on `scripts/generate_sample_reports.py`.'

issue "Enable a branch ruleset on main" \
  "area:ci,type:chore,priority:high" "$MILESTONE" \
'Settings → Rules → Rulesets, targeting `main`:

- require a pull request before merging
- require the CI status check to pass
- block force pushes
- block deletion

A solo project still benefits: it stops a late-night force push from rewriting the
history that the CHANGELOG describes.'

issue "Enable secret scanning and push protection" \
  "type:security,priority:high" "$MILESTONE" \
'Settings → Code security. Enable secret scanning **and** push protection.

The repository has been handled carefully — `.env` is gitignored and verified, test
fixtures use tokens shaped unlike real Atlassian credentials, and no real cloud id is
committed — but push protection is the control that survives a tired evening.

If it flags something in the existing twelve commits, do not use the bypass link:
history rewriting is the correct response to a real committed credential.'

issue "Resolve the IP position before making the repository public" \
  "type:chore,priority:high" "$MILESTONE" \
'The repository is private pending a decision on whether any part of it derives from
employer work. JiraLens was written from the public Jira Cloud REST API and contains
no employer code, configuration, customer data or site identifiers — but the decision
is a conversation to have deliberately, not an assumption to act on.

Until then: private, and no employer credential or sandbox ever reaches it.

**Done when** the position is settled and recorded, one way or the other.'

issue "Tag v0.1.0 and publish the release" \
  "type:chore,priority:high" "$MILESTONE" \
'Depends on every other issue in this milestone.

1. Move the `[Unreleased]` section of `CHANGELOG.md` under `## [0.1.0] - YYYY-MM-DD`
2. Confirm `__version__` and `pyproject.toml` agree
3. `git tag -a v0.1.0 -m "..." && git push origin v0.1.0`
4. `gh release create v0.1.0 --notes-from-tag`

The "Found by the first live scan" section stays in the release notes. It is the most
useful thing in the changelog: it shows what passing tests could not see.'

issue "scan --format should match demo --format" \
  "area:cli,type:feature,priority:medium" "$MILESTONE" \
'`demo` takes `--format csv|json|html` (repeatable); `scan` writes all three with no
way to narrow them. The two commands share `_render_result` and should share their
output options too.

Move the option into a shared definition so the next option added cannot drift.'

# --- backlog -----------------------------------------------------------------

issue "Exercise retry and Retry-After against a real rate limit" \
  "area:client,type:test,priority:medium,unproven-in-production" "" \
'Retry on 429/500/502/503/504 with jittered backoff, and verbatim honouring of a
numeric `Retry-After`, are covered by mocks only. No live scan has ever been rate
limited.

Either drive a real 429 against a large project, or record a live 429 response and
replay it. Confirm `Retry-After` is honoured as sent, and that 400/401/403/404 are
still never retried.'

issue "Prove the overdue check against live issues with due dates" \
  "area:checks,type:test,priority:medium,unproven-in-production" "" \
'JTS has no issues with due dates, so `overdue` has returned zero findings on every
live run — indistinguishable from a check that is silently broken, which is exactly
the failure mode the changelog parse bug had.

Include the boundary explicitly: an issue due **today** is not overdue, and "today"
comes from the configured display timezone (`Africa/Lusaka`), not the server clock.
Set a due date of today and one of yesterday, and confirm only the second is flagged.'

issue "Prove stale and high_priority_stale against aged issues" \
  "area:checks,type:test,priority:medium,unproven-in-production" "" \
'Both checks read Jira'"'"'s `updated` field and both have returned zero live findings,
because every JTS issue was created recently.

Needs a project with genuine history. Confirm the exclusive-threshold decision holds
against real data: at 30 days, an issue last updated exactly 30 calendar days ago is
not stale; 31 days is.'

issue "Add CONTRIBUTING.md" \
  "area:docs,type:chore,priority:low" "" \
'Cover: Python 3.12+ and pip 21.3+, `pip install -e ".[dev]"`, `pytest`, `ruff check`,
and the two invariants a contributor must not break —

1. no check may call `datetime.now()`; the clock arrives as `ScanContext.now`
2. widening `READ_ONLY_ENDPOINTS` requires updating the test that asserts its length,
   which is deliberate'

issue "Add issue and pull request templates" \
  "area:docs,type:chore,priority:low" "" \
'`.github/ISSUE_TEMPLATE/bug_report.yml` should ask for the `scan_id`, the JSON
report'"'"'s `schema_version`, and the warnings block — those three make most reports
reproducible without a back-and-forth.

The PR template should carry one checkbox that matters: *does this change what a
finding means?* If yes, the CHANGELOG entry is not optional.'

issue "Publish a JSON Schema for the JSON report" \
  "area:reports,type:feature,priority:low" "" \
'`SCHEMA_VERSION = "1.0"` is asserted in tests but the schema itself is only implied by
the writer. Anything consuming the report downstream has nothing to validate against.

Add `schemas/report-1.0.json` and a test that validates the generated sample report
against it, so the version number and the shape cannot drift apart.'

issue "Surface .env configuration warnings in the dashboard" \
  "area:dashboard,type:feature,priority:low" "" \
'`env_file_problems()` runs before every credential-using CLI command, and catches the
two failures that cost the most time during setup: a line with no `=` (pasting over a
placeholder usually deletes the `KEY=` with it) and an exported environment variable
shadowing the file.

The Streamlit app calls neither. Live mode there can still fail with a bare 401 while
the CLI explains the same situation clearly.'

issue "Benchmark throughput and document expectations" \
  "area:client,type:chore,priority:low" "" \
'Nobody knows how long a 5,000-issue scan takes. Changelogs batch at 1,000 ids and
`search/jql` is sequential by design — token pagination cannot be parallelised,
because page N+1'"'"'s token only arrives with page N.

Measure wall-clock against a large project, record it in the README, and decide
whether a progress indicator in the CLI is worth having. `ScanProgress` already emits
the stages.'

echo
echo "Done."
[ -n "$DRY_RUN" ] || echo "Review: gh issue list --repo $REPO --milestone '$MILESTONE'"
