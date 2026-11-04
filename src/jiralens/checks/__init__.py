"""Audit checks.

Phase 2. The protocol is fixed now so the fixtures and tests can be written against
it: every check receives the issues plus a ``ScanContext`` carrying the injected
clock, and returns findings without mutating its input.

    class AuditCheck(Protocol):
        id: str
        name: str
        def evaluate(
            self, issues: Sequence[IssueRecord], ctx: ScanContext
        ) -> list[Finding]: ...
"""

__all__: list[str] = []
