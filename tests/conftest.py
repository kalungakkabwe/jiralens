"""Shared fixtures.

``FROZEN_NOW`` is the clock the whole suite runs against. Nothing here reads the
wall clock, which is the point of injecting ``now`` into ``ScanContext``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from jiralens.models import ScanConfig, ScanContext

LUSAKA = ZoneInfo("Africa/Lusaka")
CHICAGO = ZoneInfo("America/Chicago")

#: A Wednesday, mid-morning, well away from any month or year boundary.
FROZEN_NOW = datetime(2026, 11, 4, 10, 30, tzinfo=LUSAKA)

BASE_URL = "https://demo-site.example.net"


@pytest.fixture
def now() -> datetime:
    return FROZEN_NOW


@pytest.fixture
def config() -> ScanConfig:
    return ScanConfig(project_key="DEMO", timezone="Africa/Lusaka")


@pytest.fixture
def ctx(config: ScanConfig) -> ScanContext:
    return ScanContext(now=FROZEN_NOW, config=config)


@pytest.fixture
def raw_issue() -> dict[str, Any]:
    """A complete, well-formed Jira issue payload. Tests mutate copies of this."""
    return {
        "id": "10001",
        "key": "DEMO-1",
        "fields": {
            "summary": "Renew customer reporting certificate",
            "issuetype": {"name": "Task"},
            "project": {"key": "DEMO", "name": "Demo Delivery"},
            "status": {
                "name": "In Progress",
                "statusCategory": {"key": "indeterminate", "name": "In Progress"},
            },
            "assignee": {"accountId": "acc-123", "displayName": "K. Mwale"},
            "priority": {"name": "High"},
            "created": "2026-06-01T08:00:00.000-0500",
            "updated": "2026-07-01T09:00:00.000-0500",
            "duedate": "2026-07-15",
            "resolution": None,
            "resolutiondate": None,
        },
    }
