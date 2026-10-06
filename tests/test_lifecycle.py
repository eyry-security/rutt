"""Host lifecycle: discovered -> probed -> reviewed. State only advances."""

from datetime import datetime, timezone


def test_discover_sets_state_and_timestamps(r):
    assert r.add_host("new.example.com", scope="*.example.com", source="certstream") is True
    row = r.host_detail("new.example.com")["host"]
    assert row["state"] == "discovered"
    assert row["first_seen"] is not None
    assert row["last_seen"] is not None
    assert row["last_probed_at"] is None
    assert row["last_reviewed_at"] is None
    assert row["scope"] == "*.example.com"
    assert row["source"] == "certstream"


def test_rediscover_returns_false_and_refreshes_last_seen(r):
    r.add_host("dup.example.com")
    first = r.host_detail("dup.example.com")["host"]["last_seen"]
    assert r.add_host("dup.example.com") is False
    second = r.host_detail("dup.example.com")["host"]["last_seen"]
    assert second >= first
    # only one discover scan logged
    scans = r.query_scans(host="dup.example.com", kind="discover")
    assert len(scans) == 1


def test_probe_advances_to_probed_and_stamps(r):
    r.add_host("p.example.com")
    r.add_probe({"host": "p.example.com", "scheme": "https", "port": 443,
                 "status": 200, "ok": True})
    row = r.host_detail("p.example.com")["host"]
    assert row["state"] == "probed"
    assert row["last_probed_at"] is not None


def test_review_advances_to_reviewed(r):
    r.add_host("rv.example.com")
    r.review("rv.example.com", tool="aplomado", ok=True)
    row = r.host_detail("rv.example.com")["host"]
    assert row["state"] == "reviewed"
    assert row["last_reviewed_at"] is not None


def test_finding_advances_to_reviewed(r):
    r.add_host("f.example.com")
    fid = r.add_finding("XSS", host="f.example.com", severity="medium")
    assert isinstance(fid, int) and fid > 0
    assert r.host_detail("f.example.com")["host"]["state"] == "reviewed"


def test_state_never_regresses(r, seeded):
    # re-probing a reviewed host keeps it reviewed
    r.add_probe({"host": "api.example.com", "scheme": "https", "port": 443,
                 "status": 200, "ok": True})
    row = r.host_detail("api.example.com")["host"]
    assert row["state"] == "reviewed"
    assert row["last_probed_at"] is not None  # probe still stamps the clock


def test_full_lifecycle_ordering(r):
    r.add_host("life.example.com", scope="*.example.com", source="certstream")
    r.add_probe({"host": "life.example.com", "scheme": "http", "port": 80,
                 "status": 301, "ok": True})
    r.review("life.example.com")
    scans = r.query_scans(host="life.example.com")
    kinds = [s["kind"] for s in scans]
    assert kinds.count("discover") == 1
    assert kinds.count("probe") == 1
    assert kinds.count("review") == 1
    # timestamps move forward through the lifecycle
    h = r.host_detail("life.example.com")["host"]
    assert h["first_seen"] <= h["last_probed_at"] <= h["last_reviewed_at"]


def test_tags_merge_on_rediscover(r):
    r.add_host("t.example.com", tags=["web"])
    r.add_host("t.example.com", tags=["api", "web"])
    row = r.host_detail("t.example.com")["host"]
    assert sorted(row["tags"]) == ["api", "web"]


def test_scope_source_preserved_when_not_resupplied(r):
    r.add_host("s.example.com", scope="*.example.com", source="certstream")
    r.add_host("s.example.com")  # no scope/source -> keep old
    row = r.host_detail("s.example.com")["host"]
    assert row["scope"] == "*.example.com"
    assert row["source"] == "certstream"
