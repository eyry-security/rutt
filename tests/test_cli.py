"""CLI basics: every subcommand, wired to the test database."""

import json
import os

import pytest

from rutt.cli import main

TEST_DSN = os.environ.get("RUTT_TEST_DSN", "postgresql:///rutt_test")


@pytest.fixture(autouse=True)
def _dsn_env(monkeypatch, r):
    monkeypatch.setenv("RUTT_DSN", TEST_DSN)


_capsys = None

def run(*argv):
    # drain any output from previous commands so capsys.readouterr()
    # after this call sees only this command's output
    if _capsys is not None:
        _capsys.readouterr()
    rc = main([*argv, "--dsn", TEST_DSN])
    assert rc == 0
    return rc


@pytest.fixture(autouse=True)
def _wire_capsys(capsys):
    global _capsys
    _capsys = capsys


def test_init_is_idempotent(r, capsys):
    assert main(["init", "--dsn", TEST_DSN]) == 0
    assert main(["init", "--dsn", TEST_DSN]) == 0  # safe to re-run
    assert "schema ready" in capsys.readouterr().out


def test_add_host_and_hosts_table(r, capsys):
    run("add", "host", "cli.example.com", "--scope", "*.example.com",
        "--source", "certstream", "--tag", "web")
    run("hosts", "--json")
    rows = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert len(rows) == 1
    assert rows[0]["host"] == "cli.example.com"
    assert rows[0]["state"] == "discovered"


def test_add_finding(r, capsys):
    run("add", "host", "cf.example.com")
    run("add", "finding", "Open S3 bucket", "--host", "cf.example.com",
        "--severity", "critical", "--source", "manual",
        "--data", '{"bucket": "x"}')
    out = capsys.readouterr().out
    assert "finding #" in out
    run("findings", "--severity", "critical", "--json")
    rows = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert rows[0]["data"]["bucket"] == "x"
    # host advanced to reviewed
    run("host", "cf.example.com", "--json")
    detail = json.loads(capsys.readouterr().out)
    assert detail["host"]["state"] == "reviewed"


def test_review_command(r, capsys):
    run("add", "host", "rev.example.com")
    run("review", "rev.example.com", "--note", "looks clean")
    run("host", "rev.example.com", "--json")
    detail = json.loads(capsys.readouterr().out)
    assert detail["host"]["state"] == "reviewed"
    kinds = [s["kind"] for s in detail["scans"]]
    assert "review" in kinds


def test_ingest_vedette_stdin(r, capsys, monkeypatch):
    import io
    line = json.dumps({"host": "stdin.example.com", "scheme": "https",
                       "port": 443, "status": 200, "ok": True})
    monkeypatch.setattr("sys.stdin", io.StringIO(line + "\n"))
    run("ingest", "vedette", "-")
    assert "probes" in capsys.readouterr().out
    run("probes", "--host", "stdin.example.com", "--json")
    rows = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert rows[0]["status"] == 200


def test_ingest_foretop_file(r, capsys, tmp_path):
    f = tmp_path / "hosts.jsonl"
    f.write_text('{"host": "file.example.com", "scope": "*.example.com"}\n')
    run("ingest", "foretop", str(f))
    run("hosts", "--search", "file.example", "--json")
    rows = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert rows[0]["host"] == "file.example.com"


def test_scans_and_stats(r, capsys):
    run("add", "host", "st.example.com", "--source", "certstream")
    run("scans", "--host", "st.example.com", "--json")
    rows = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert rows[0]["kind"] == "discover"
    run("stats")
    out = capsys.readouterr().out
    assert "hosts=1" in out and "scans=1" in out


def test_sql_readonly(r, capsys):
    run("add", "host", "q.example.com")
    run("sql", "SELECT host FROM hosts", "--json")
    rows = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    assert rows[0]["host"] == "q.example.com"
    # writes are rejected with exit code 2
    assert main(["sql", "DELETE FROM hosts", "--dsn", TEST_DSN]) == 2


def test_host_not_found_exits_1(r, capsys):
    assert main(["host", "missing.example.com", "--dsn", TEST_DSN]) == 1


def test_table_output_format(r, capsys):
    run("add", "host", "tbl.example.com")
    run("hosts")
    out = capsys.readouterr().out
    assert "tbl.example.com" in out
    assert "row(s)" in out
