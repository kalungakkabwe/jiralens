"""The seeding script must stay outside the read-only boundary.

``scripts/seed_test_project.py`` is the only file in this repository that writes to
Jira. The read-only guarantee rests on ``jiralens.jira.client`` refusing every verb
and path outside a five-entry allowlist — a guarantee that would be quietly worth
less if a write script shared that client, or if write helpers drifted into the
package itself.

These tests are cheap and they fail loudly on exactly the change that would matter.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SEED_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_test_project.py"

#: The only module the seeder may take from the package. Settings parsing is inert:
#: it reads environment variables and has no capability to reach Jira at all.
ALLOWED_PACKAGE_IMPORTS = frozenset({"jiralens.config"})

#: POST is deliberately absent. Two of Jira's *read* operations are POSTs —
#: ``search/jql`` and ``changelog/bulkfetch`` — so treating POST as a write verb
#: would flag the read-only client's own allowlist. The verb does not determine
#: whether a call writes; the endpoint does, which is why the allowlist pairs them.
WRITE_VERBS = ("PUT", "DELETE", "PATCH")


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.fixture(scope="module")
def seed_tree() -> ast.AST:
    assert SEED_SCRIPT.is_file(), f"{SEED_SCRIPT} is missing"
    return ast.parse(SEED_SCRIPT.read_text(encoding="utf-8"))


def test_seed_script_does_not_import_the_read_only_client(seed_tree: ast.AST) -> None:
    """If this ever fails, the allowlist no longer describes the whole codebase."""
    package_imports = {m for m in _imported_modules(seed_tree) if m.startswith("jiralens")}
    offenders = package_imports - ALLOWED_PACKAGE_IMPORTS
    assert not offenders, (
        f"scripts/seed_test_project.py imports {sorted(offenders)}. It writes to Jira "
        "and must not share anything with the read-only client."
    )


def test_seed_script_names_no_client_symbol(seed_tree: ast.AST) -> None:
    """Identifiers only — the docstring names JiraClient precisely to disclaim it."""
    used = {node.id for node in ast.walk(seed_tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(seed_tree) if isinstance(node, ast.Attribute)
    }
    forbidden = {"JiraClient", "READ_ONLY_ENDPOINTS", "ReadOnlyViolationError"} & used
    assert not forbidden, f"seeder references {sorted(forbidden)}"


def test_package_contains_no_write_verbs() -> None:
    """The converse: no write verb may appear in the package the seeder sits beside.

    Scoped to string literals, so a word like 'POSTal' in prose cannot trip it and a
    genuine ``client.request("PUT", ...)`` cannot hide.
    """
    package = SEED_SCRIPT.resolve().parents[1] / "src" / "jiralens"
    offenders: list[str] = []

    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in WRITE_VERBS:
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")

    assert not offenders, (
        "write verbs appear as literals inside the jiralens package: " + ", ".join(offenders)
    )
