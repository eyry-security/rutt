"""Findings: storage, retrieval, and their effect on the host lifecycle."""


def test_add_finding_returns_id_and_stores(r):
    fid = r.add_finding("Exposed .git", host="h.example.com", severity="high",
                        source="aplomado", description="git dir listing",
                        data={"path": "/.git/HEAD"})
    rows = r.query_findings(host="h.example.com")
    assert len(rows) == 1
    f = rows[0]
    assert f["id"] == fid
    assert f["title"] == "Exposed .git"
    assert f["severity"] == "high"
    assert f["source"] == "aplomado"
    assert f["description"] == "git dir listing"
    assert f["data"]["path"] == "/.git/HEAD"
    assert f["found_at"] is not None


def test_finding_without_host(r):
    fid = r.add_finding("Orphan note", severity="info")
    assert fid > 0
    rows = r.query_findings(severity="info")
    assert len(rows) == 1
    assert rows[0]["host"] is None


def test_finding_creates_host_if_unknown(r):
    r.add_finding("Something", host="ghost.example.com", severity="low")
    assert r.host_detail("ghost.example.com")["host"]["state"] == "reviewed"


def test_query_findings_filters(r):
    r.add_finding("A", host="x.example.com", severity="high", source="aplomado")
    r.add_finding("B", host="x.example.com", severity="low", source="manual")
    r.add_finding("C", host="y.example.com", severity="high", source="aplomado")
    assert len(r.query_findings(severity="high")) == 2
    assert len(r.query_findings(host="x.example.com")) == 2
    assert len(r.query_findings(source="manual")) == 1
    assert len(r.query_findings(host="y.example.com", severity="high")) == 1


def test_host_detail_includes_findings(r, seeded):
    detail = r.host_detail("api.example.com")
    assert len(detail["findings"]) == 1
    assert detail["findings"][0]["title"] == "Exposed .git"
    assert detail["findings"][0]["id"] == seeded["finding_id"]
