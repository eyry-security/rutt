"""Query patterns from the README: the questions Rutt exists to answer."""


def _populate(r):
    r.add_host("api.example.com", scope="*.example.com", source="certstream")
    r.add_host("blog.example.com", scope="*.example.com", source="certstream")
    r.add_host("old.example.com", scope="*.other.com", source="certstream")
    r.add_probe({"host": "api.example.com", "scheme": "https", "port": 443,
                 "status": 200, "server": "nginx", "tech": ["nginx"], "ok": True})
    r.add_probe({"host": "blog.example.com", "scheme": "https", "port": 443,
                 "status": 200, "server": "apache", "tech": ["apache"], "ok": True})
    r.add_probe({"host": "old.example.com", "scheme": "http", "port": 80,
                 "status": 404, "server": "nginx", "tech": ["nginx"], "ok": True})
    r.add_finding("Exposed .git", host="api.example.com", severity="high")


def test_readme_query_in_scope_nginx_200(r):
    """'what was in scope, running nginx, and returning a 200?'"""
    _populate(r)
    rows = r.query_hosts(scope="*.example.com", tech="nginx", status=200)
    assert [x["host"] for x in rows] == ["api.example.com"]


def test_query_hosts_by_state(r):
    _populate(r)
    assert {x["host"] for x in r.query_hosts(state="discovered")} == set()
    # api is reviewed (finding), blog+old are probed
    assert {x["host"] for x in r.query_hosts(state="reviewed")} == {"api.example.com"}
    assert {x["host"] for x in r.query_hosts(state="probed")} == \
        {"blog.example.com", "old.example.com"}


def test_query_hosts_search_and_source(r):
    _populate(r)
    assert len(r.query_hosts(search="blog")) == 1
    assert len(r.query_hosts(source="certstream")) == 3
    assert len(r.query_hosts(scope="*.other.com")) == 1


def test_stats(r):
    _populate(r)
    s = r.stats()
    assert s["hosts"] == 3
    assert s["probed"] == 2
    assert s["reviewed"] == 1
    assert s["discovered"] == 0
    assert s["probes"] == 3
    assert s["responded"] == 3
    assert s["findings"] == 1
    assert s["scans"] == 3 + 3 + 1  # discover x3, probe x3, review x1
    assert s["scopes"] == 2


def test_host_detail_shape(r, seeded):
    d = r.host_detail("api.example.com")
    assert set(d) == {"host", "probes", "findings", "scans"}
    assert d["host"]["state"] == "reviewed"
    assert len(d["probes"]) == 1
    assert len(d["findings"]) == 1
    assert len(d["scans"]) == 3
    assert r.host_detail("nope.example.com") is None


def test_read_sql_allows_select_rejects_writes(r):
    _populate(r)
    rows = r.read_sql("SELECT host FROM hosts WHERE scope='*.example.com'")
    assert len(rows) == 2
    import pytest
    for bad in ("INSERT INTO hosts (host) VALUES ('x')",
                "UPDATE hosts SET state='x'",
                "DELETE FROM hosts",
                "DROP TABLE hosts",
                "SELECT * FROM hosts; DELETE FROM hosts"):
        with pytest.raises(ValueError):
            r.read_sql(bad)
    # non-SELECT rejected
    with pytest.raises(ValueError):
        r.read_sql("VACUUM hosts")


def test_ingest_vedette(r):
    lines = [
        '{"host": "i1.example.com", "scheme": "https", "port": 443, "status": 200, "ok": true}',
        '{"host": "i2.example.com", "scheme": "http", "port": 80, "ok": false, "error": "timeout"}',
        "not json at all",
        "",
    ]
    res = r.ingest_vedette(lines)
    assert res == {"probes": 2, "responded": 1, "bad_lines": 1}
    assert r.host_detail("i1.example.com")["host"]["state"] == "probed"


def test_ingest_foretop(r):
    lines = [
        '{"host": "f1.example.com", "scope": "*.example.com", "source": "certstream"}',
        '{"host": "f1.example.com", "scope": "*.example.com"}',  # dup -> not new
        '{"nope": true}',
        "garbage",
    ]
    res = r.ingest_foretop(lines)
    assert res == {"hosts": 2, "new": 1, "bad_lines": 2}
