"""Fixtures: a fresh rutt_test Postgres database per test."""

import os

import pytest

from rutt import Rutt

TEST_DSN = os.environ.get("RUTT_TEST_DSN", "postgresql:///rutt_test")


@pytest.fixture()
def r():
    """Rutt connected to the test DB, schema initialised, tables empty."""
    q = Rutt(TEST_DSN)
    q.init_schema()
    with q.conn.cursor() as cur:
        for table in ("scans", "findings", "probes", "hosts"):
            cur.execute(f"TRUNCATE {table} RESTART IDENTITY")
    yield q
    q.close()


@pytest.fixture()
def seeded(r):
    """A host that has been through the full lifecycle."""
    r.add_host("api.example.com", scope="*.example.com", source="certstream")
    r.add_probe({
        "host": "api.example.com", "scheme": "https", "port": 443,
        "status": 200, "server": "nginx", "title": "Example API",
        "tech": ["nginx"], "ips": ["93.184.216.34"], "ok": True,
    })
    fid = r.add_finding("Exposed .git", host="api.example.com", severity="high",
                        source="aplomado", data={"path": "/.git/HEAD"})
    return {"finding_id": fid}
