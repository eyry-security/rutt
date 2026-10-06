"""The scans table is append-only: rows are only ever inserted, never
updated or deleted, and every lifecycle stage writes to it."""


def test_every_stage_logs_a_scan(r):
    r.add_host("s.example.com", scope="*.example.com", source="certstream")
    r.add_probe({"host": "s.example.com", "scheme": "https", "port": 443, "ok": True})
    r.review("s.example.com")
    scans = r.query_scans(host="s.example.com")
    by_kind = {}
    for s in scans:
        by_kind.setdefault(s["kind"], []).append(s)
    assert set(by_kind) == {"discover", "probe", "review"}
    assert by_kind["discover"][0]["tool"] == "certstream"
    assert by_kind["probe"][0]["tool"] == "vedette"
    assert by_kind["review"][0]["tool"] == "aplomado"


def test_scan_ids_are_monotonic_and_immutable(r):
    r.add_host("m.example.com")
    r.add_probe({"host": "m.example.com", "scheme": "https", "port": 443})
    scans = r.query_scans(host="m.example.com")
    ids_before = sorted(s["id"] for s in scans)
    before = {s["id"]: dict(s) for s in scans}
    # more activity only appends
    r.review("m.example.com")
    scans_after = r.query_scans(host="m.example.com")
    assert len(scans_after) == len(ids_before) + 1
    for s in scans_after:
        if s["id"] in before:
            old = before[s["id"]]
            assert s["kind"] == old["kind"]
            assert s["tool"] == old["tool"]
            assert s["host"] == old["host"]
            assert s["scanned_at"] == old["scanned_at"]


def test_no_update_or_delete_path_in_library(r):
    # the store's public surface only inserts/appends scans
    public = [m for m in dir(r) if not m.startswith("_")]
    assert not any(x in public for x in ("delete_scan", "update_scan", "remove_scan",
                                         "delete_host", "purge", "clear"))


def test_query_scans_filters(r, seeded):
    assert len(r.query_scans(host="api.example.com")) == 3
    assert len(r.query_scans(kind="probe")) == 1
    assert len(r.query_scans(tool="aplomado")) == 1
    assert len(r.query_scans(host="api.example.com", kind="discover")) == 1


def test_scan_detail_carries_context(r, seeded):
    probes = r.query_scans(host="api.example.com", kind="probe")
    assert probes[0]["detail"]["status"] == 200
    reviews = r.query_scans(host="api.example.com", kind="review")
    assert reviews[0]["detail"]["finding_id"] == seeded["finding_id"]
    assert reviews[0]["detail"]["severity"] == "high"


def test_tail_helpers(r, seeded):
    assert r.max_id("scans") == 3
    assert r.max_id("probes") == 1
    rows = r.rows_after("scans", 1)
    assert [x["id"] for x in rows] == [2, 3]  # oldest first
    assert r.rows_after("scans", 99) == []
    # bad table names are rejected (no SQL injection via table arg)
    import pytest
    with pytest.raises(ValueError):
        r.max_id("hosts; DROP TABLE hosts;")
    with pytest.raises(ValueError):
        r.rows_after("scans; DELETE FROM scans;", 0)
