#!/usr/bin/env python3
"""Seed a Jira project with varied, reproducible test data.

*** THIS SCRIPT WRITES TO JIRA. Nothing else in this repository does. ***

JiraLens is read-only and enforces it in ``jiralens.jira.client``, whose allowlist
holds five read operations and refuses every other verb and path. This script is
deliberately **not** part of that package: it builds its own ``httpx`` client and
calls write endpoints directly. It never imports ``JiraClient``, and
``tests/test_seed_isolation.py`` asserts that, so the read-only guarantee is not
quietly widened by a file sitting next to the thing it describes.

Why it exists
-------------
Five audit checks are correct in tests and have never flagged anything against a
real site, because the only project ever scanned holds forty-nine recent,
near-identical issues. Pagination has never reached a second page, ``overdue`` has
never seen a due date, and ``stale`` has never seen an inactive issue. That is the
same blind spot that let the epoch-milliseconds defect survive 632 passing tests:
plausible output, no contradicting evidence.

This produces the contradicting evidence.

What it cannot do
-----------------
Jira will not let any API backdate ``created`` or ``updated``. Seeded issues are
therefore new, and ``stale`` / ``high_priority_stale`` can only be exercised here
by lowering their thresholds to zero. That proves the code path; it does not prove
behaviour against genuinely aged data, and the difference should be stated plainly
when closing the relevant issues rather than glossed over.

Usage
-----
    python scripts/seed_test_project.py --project JLT            # preview only
    python scripts/seed_test_project.py --project JLT --apply    # writes
"""

from __future__ import annotations

import argparse
import base64
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from jiralens.config import load_settings  # settings only — never the client

BULK_LIMIT = 50  # Atlassian's documented maximum for POST /rest/api/3/issue/bulk
DEFAULT_SEED = 20260905

# Deliberately hostile summaries. Both are safe in Jira and both are exactly what
# an export has to survive: the first executes in Excel if the CSV writer does not
# escape it, the second renders as markup if the HTML template does not.
ADVERSARIAL_SUMMARIES = (
    '=HYPERLINK("http://example.invalid","claim your refund")',
    "<script>alert('jiralens')</script> payment gateway returns 500",
)

SUBJECTS = (
    "Checkout returns 500 under concurrent load",
    "Password reset email never arrives",
    "Search results ignore the date filter",
    "Audit log missing actor for bulk edits",
    "Timezone handling wrong for Africa/Lusaka",
    "Session expires mid-upload and loses the file",
    "Webhook retries duplicate on timeout",
    "Permission scheme drift between environments",
    "Report export truncates at 1000 rows",
    "SLA clock keeps running after resolution",
    "Attachment preview fails for PDFs over 10MB",
    "Mobile layout overlaps the action bar",
    "Rate limit returns 200 with an empty body",
    "Custom field value lost on issue move",
    "Automation rule fires twice on transition",
)

QUALIFIERS = (
    "in production",
    "for admin accounts",
    "after the November release",
    "on the staging tenant",
    "for externally shared links",
    "when SSO is enforced",
    "under the new permission scheme",
    "for archived projects",
)

LABEL_POOL = ("seeded", "regression", "customer-reported", "infra", "ux", "security")


@dataclass
class Plan:
    """What the script intends to create. Printed in full before anything is written."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    transition_indices: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_client(timeout: float) -> tuple[httpx.Client, str]:
    """An httpx client authenticated the same way JiraLens authenticates — no more."""
    settings = load_settings()
    api_base, email, token = settings.require_live()
    secret = token.get_secret_value()

    if settings.auth_scheme == "bearer":
        auth_header = f"Bearer {secret}"
    else:
        if not email:
            raise SystemExit("JIRA_EMAIL is required for basic auth.")
        raw = f"{email}:{secret}".encode()
        auth_header = f"Basic {base64.b64encode(raw).decode()}"

    client = httpx.Client(
        base_url=api_base,
        timeout=timeout,
        headers={
            "Authorization": auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    return client, (settings.site_url or api_base)


def api(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    """One request, with the error body surfaced rather than swallowed.

    Jira's write endpoints return the actual reason in the body. A bare
    ``raise_for_status()`` would hide "Field 'duedate' cannot be set" behind a 400.
    """
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise SystemExit(
            f"{method} {path} -> {response.status_code}\n{response.text[:1500]}"
        )
    if not response.content:
        return None
    return response.json()


def preflight(client: httpx.Client, project: str) -> tuple[str, dict[str, Any], set[str]]:
    """Confirm the project, pick an issue type, and discover which fields are usable.

    Creating 150 issues whose due dates were silently dropped because the field is
    not on the create screen would reproduce exactly the failure this script exists
    to prevent: a confident result with nothing behind it. So the fields are checked
    first and the run aborts if the ones that matter are missing.
    """
    meta = api(client, "GET", f"/rest/api/3/project/{project}")
    print(f"  project: {meta['key']} — {meta['name']} ({meta.get('style', 'unknown')}-managed)")

    types = api(client, "GET", f"/rest/api/3/issue/createmeta/{project}/issuetypes")
    candidates = [t for t in types.get("issueTypes", []) if not t.get("subtask")]
    if not candidates:
        raise SystemExit(f"No non-subtask issue types available in {project}.")

    preferred = next((t for t in candidates if t["name"] in ("Task", "Story")), candidates[0])
    print(f"  issue type: {preferred['name']} (id {preferred['id']})")

    fields_doc = api(
        client,
        "GET",
        f"/rest/api/3/issue/createmeta/{project}/issuetypes/{preferred['id']}",
    )
    available = {f["fieldId"] for f in fields_doc.get("fields", [])}

    missing = [f for f in ("duedate", "priority", "labels") if f not in available]
    if "duedate" in missing:
        raise SystemExit(
            "The 'Due date' field is not on this project's create screen, so the "
            "overdue check cannot be exercised here. Add it to the issue layout in "
            "Project settings, then re-run."
        )
    for name in missing:
        print(f"  note: '{name}' unavailable — those variations will be skipped")

    return preferred["id"], meta, available


def build_plan(
    *,
    count: int,
    issue_type_id: str,
    project: str,
    available: set[str],
    account_id: str | None,
    transition_fraction: float,
    seed: int,
    today: datetime,
) -> Plan:
    """Compose the issues deterministically. Same seed, same data, every time."""
    rng = random.Random(seed)
    plan = Plan()

    priorities = ("Highest", "High", "Medium", "Low", "Lowest")

    for index in range(count):
        if index < len(ADVERSARIAL_SUMMARIES):
            summary = ADVERSARIAL_SUMMARIES[index]
        else:
            summary = f"{rng.choice(SUBJECTS)} {rng.choice(QUALIFIERS)} [{index:03d}]"

        fields: dict[str, Any] = {
            "project": {"key": project},
            "issuetype": {"id": issue_type_id},
            "summary": summary[:250],
        }

        # Due dates, weighted so every branch of the overdue check is hit — including
        # the boundary that matters most: due *today* is not overdue.
        roll = rng.random()
        if roll < 0.22:
            due = today - timedelta(days=rng.randint(1, 75))
        elif roll < 0.28:
            due = today  # the boundary case
        elif roll < 0.45:
            due = today + timedelta(days=rng.randint(1, 45))
        else:
            due = None
        if due is not None:
            fields["duedate"] = due.date().isoformat()

        if "priority" in available:
            fields["priority"] = {
                "name": rng.choices(priorities, weights=(8, 18, 45, 20, 9))[0]
            }

        if "labels" in available:
            fields["labels"] = ["seeded"] + rng.sample(
                [lbl for lbl in LABEL_POOL if lbl != "seeded"], k=rng.randint(0, 2)
            )

        # Roughly 45% unassigned, so the unassigned check has real work to find.
        # Only one account id is knowable without a user-search call, which is
        # enough: the check tests for absence, not for who.
        if account_id and rng.random() > 0.45:
            fields["assignee"] = {"id": account_id}

        plan.issues.append({"fields": fields})

    wanted = int(count * transition_fraction)
    plan.transition_indices = sorted(rng.sample(range(count), k=min(wanted, count)))
    return plan


def summarise(plan: Plan) -> None:
    due_past = due_today = due_future = due_none = unassigned = 0
    priorities: Counter[str] = Counter()
    today = datetime.now(tz=UTC).date().isoformat()

    for item in plan.issues:
        fields = item["fields"]
        value = fields.get("duedate")
        if value is None:
            due_none += 1
        elif value < today:
            due_past += 1
        elif value == today:
            due_today += 1
        else:
            due_future += 1
        if "assignee" not in fields:
            unassigned += 1
        if "priority" in fields:
            priorities[fields["priority"]["name"]] += 1

    print(f"\n  {len(plan.issues)} issues")
    print(f"    overdue (due in the past): {due_past}")
    print(f"    due today (must NOT flag): {due_today}")
    print(f"    due in the future:         {due_future}")
    print(f"    no due date:               {due_none}")
    print(f"    unassigned:                {unassigned}")
    if priorities:
        print("    priorities: " + ", ".join(f"{k}={v}" for k, v in priorities.most_common()))
    print(f"    will be transitioned:      {len(plan.transition_indices)}")
    print(f"    adversarial summaries:     {len(ADVERSARIAL_SUMMARIES)} (CSV formula, HTML markup)")


def create_issues(client: httpx.Client, plan: Plan) -> list[str]:
    created: list[str] = []
    batches = range(0, len(plan.issues), BULK_LIMIT)
    for start in batches:
        chunk = plan.issues[start : start + BULK_LIMIT]
        body = api(client, "POST", "/rest/api/3/issue/bulk", json={"issueUpdates": chunk})
        created.extend(issue["key"] for issue in body.get("issues", []))
        for err in body.get("errors", []):
            print(f"    error on element {err.get('failedElementNumber')}: "
                  f"{err.get('elementErrors', {}).get('errors')}")
        print(f"    created {len(created)}/{len(plan.issues)}")
        time.sleep(1.0)  # courtesy pause; this is someone's real site
    return created


def apply_transitions(client: httpx.Client, keys: list[str], indices: list[int]) -> int:
    """Move a subset of issues so the changelog has genuine transitions in it.

    This is the path the epoch-milliseconds defect lived on. Synthetic fixtures
    agreed with the bug; a real transition is what exposed it.
    """
    moved = 0
    for position, index in enumerate(indices, start=1):
        if index >= len(keys):
            continue
        key = keys[index]
        available = api(client, "GET", f"/rest/api/3/issue/{key}/transitions")
        options = [
            t for t in available.get("transitions", [])
            if t.get("to", {}).get("statusCategory", {}).get("key") != "done"
        ] or available.get("transitions", [])
        if not options:
            continue
        api(
            client,
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": options[0]["id"]}},
        )
        moved += 1
        if position % 10 == 0:
            print(f"    transitioned {moved}/{len(indices)}")
        time.sleep(0.25)
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", required=True, help="Target project key, e.g. JLT")
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--transition-fraction", type=float, default=0.45)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without it the plan is printed and nothing is created.",
    )
    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error("--count must be at least 1")
    if not 0.0 <= args.transition_fraction <= 1.0:
        parser.error("--transition-fraction must be between 0 and 1")

    client, site_url = build_client(args.timeout)
    with client:
        print(f"Site: {site_url}")
        me = api(client, "GET", "/rest/api/3/myself")
        account_id = me.get("accountId")
        print(f"  authenticated as: {me.get('displayName')}")

        issue_type_id, _, available = preflight(client, args.project)

        plan = build_plan(
            count=args.count,
            issue_type_id=issue_type_id,
            project=args.project,
            available=available,
            account_id=account_id,
            transition_fraction=args.transition_fraction,
            seed=args.seed,
            today=datetime.now(tz=UTC),
        )
        summarise(plan)

        if not args.apply:
            print("\nDry run — nothing was created. Re-run with --apply to write.")
            return 0

        print(f"\nWriting to {site_url} …")
        keys = create_issues(client, plan)
        print(f"  {len(keys)} issues created")

        moved = apply_transitions(client, keys, plan.transition_indices)
        print(f"  {moved} issues transitioned")

    print(
        f"\nDone. Next:\n"
        f"  jiralens scan --project {args.project} --output reports\n"
        f"  jiralens scan --project {args.project} --stale-days 0 "
        f"--high-priority-stale-days 0 --status-age-days 0\n\n"
        f"The second run exercises stale, high_priority_stale and status_age, which\n"
        f"cannot otherwise fire against issues created minutes ago. Treat it as proof\n"
        f"the code path works, not as proof the thresholds behave on aged data."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
