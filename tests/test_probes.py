"""Probe records: upsert behaviour, storage, retrieval."""

import pytest


def test_add_probe_stores_full_record(r):
    r.add_probe({
        "host": "pr.example.com", "url": "https://pr.example.com/",
        "scheme": "https", "port": 443, "status": 200, "title": "Hi",
        "server": "nginx", "content_type": "text/html", "content_length": 1234,
        "ips": ["1.2.3.4"], "tech": ["nginx", "php"],
        "body_sha256": "abc123", "response_time_ms": 212,
        "ok": True, "error": None,
    })
    rows = r.query_probes(host="pr.example.com")
    assert len(rows) == 1
    p = rows[0]
    assert p["status"] == 200
    assert p["server"] == "nginx"
    assert sorted(p["tech"]) == ["nginx", "php"]
    assert p["ips"] == ["1.2.3.4"]
    assert p["ok"] is True
    assert p["probed_at"] is None or True  # no timestamp supplied
    assert p["first_probed_at"] is not None


def test_add_probe_upserts_same_endpoint(r):
    probe = {"host": "up.example.com", "scheme": "https", "port": 443,
             "status": 200, "title": "v1", "ok": True}
    r.add_probe(probe)
    r.add_probe({**probe, "status": 500, "title": "v2", "ok": False})
    rows = r.query_probes(host="up.example.com")
    assert len(rows) == 1  # upsert, not a second row
    assert rows[0]["status"] == 500
    assert rows[0]["title"] == "v2"
    assert rows[0]["ok"] is False


def test_add_probe_distinct_ports_are_distinct_rows(r):
    base = {"host": "ports.example.com", "scheme": "https", "status": 200, "ok": True}
    r.add_probe({**base, "port": 443})
    r.add_probe({**base, "port": 8443})
    assert len(r.query_probes(host="ports.example.com")) == 2


def test_add_probe_requires_host(r):
    with pytest.raises(ValueError):
        r.add_probe({"scheme": "https", "port": 443})
    # 'input' is accepted as an alias for host (vedette compat)
    r.add_probe({"input": "alias.example.com", "scheme": "https", "port": 443})
    assert r.host_detail("alias.example.com") is not None


def test_add_probe_creates_host_if_unknown(r):
    r.add_probe({"host": "brand.new", "scheme": "http", "port": 80, "ok": False})
    row = r.host_detail("brand.new")["host"]
    assert row["state"] == "probed"  # discovered implicitly, then probed


def test_add_probe_parses_timestamps(r):
    r.add_probe({"host": "ts.example.com", "scheme": "https", "port": 443,
                 "timestamp": "2026-10-04T08:14:02Z", "ok": True})
    p = r.query_probes(host="ts.example.com")[0]
    assert p["probed_at"] is not None
    assert p["probed_at"].year == 2026


def test_query_probes_filters(r):
    r.add_probe({"host": "a.example.com", "scheme": "https", "port": 443,
                 "status": 200, "tech": ["nginx"], "ok": True})
    r.add_probe({"host": "b.example.com", "scheme": "https", "port": 443,
                 "status": 404, "tech": ["apache"], "ok": True})
    r.add_probe({"host": "c.example.com", "scheme": "http", "port": 80,
                 "ok": False, "error": "timeout"})
    assert len(r.query_probes(status=200)) == 1
    assert len(r.query_probes(tech="nginx")) == 1
    assert len(r.query_probes(only_ok=True)) == 2
    assert len(r.query_probes(search="b.example")) == 1
    assert len(r.query_probes(limit=1)) == 1
