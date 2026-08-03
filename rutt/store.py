"""The library: a thin, typed wrapper over Postgres that owns the host lifecycle.

Every stage writes through here, so state and history stay consistent no matter
who calls it — the CLI, the eyry orchestrator, or an Aplomado worker writing
findings back:

* discovery  -> :meth:`add_host`     (or first sight via any stage)
* probing    -> :meth:`add_probe`    (advances state to ``probed``)
* review     -> :meth:`add_finding` / :meth:`review` (advances to ``reviewed``)

Each of those also appends a row to ``scans``, so "when was this last probed /
reviewed, and by what" is always answerable. State only moves forward.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .schema import SCHEMA

DEFAULT_DSN_ENVS = ("RUTT_DSN", "DATABASE_URL")
STATE_RANK = {"discovered": 0, "probed": 1, "reviewed": 2}


def default_dsn() -> str:
    for env in DEFAULT_DSN_ENVS:
        val = os.environ.get(env)
        if val:
            return val
    return "postgresql:///rutt"


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class Rutt:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or default_dsn()
        self.conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Rutt":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def init_schema(self) -> None:
        self.conn.execute(SCHEMA)

    # -- internals ------------------------------------------------------------

    def log_scan(self, host, kind, tool=None, ok=None, detail=None) -> None:
        self.conn.execute(
            "INSERT INTO scans (host, kind, tool, ok, detail) VALUES (%s,%s,%s,%s,%s)",
            (host, kind, tool, ok, Jsonb(detail or {})),
        )

    def _upsert_host(self, host, scope=None, source=None, tags=None, tool=None) -> bool:
        """Insert or refresh a host. Returns True if this was the first sighting;
        in that case a 'discover' scan is logged automatically."""
        row = self.conn.execute(
            """
            INSERT INTO hosts (host, scope, source, tags)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (host) DO UPDATE SET
                last_seen = now(),
                scope  = COALESCE(EXCLUDED.scope,  hosts.scope),
                source = COALESCE(EXCLUDED.source, hosts.source),
                tags   = ARRAY(SELECT DISTINCT unnest(hosts.tags || EXCLUDED.tags))
            RETURNING (xmax = 0) AS inserted
            """,
            (host, scope, source, list(tags or [])),
        ).fetchone()
        inserted = bool(row["inserted"])
        if inserted:
            self.log_scan(host, "discover", tool=tool or source or "manual",
                          detail={"scope": scope} if scope else {})
        return inserted

    # -- lifecycle: discovery -------------------------------------------------

    def add_host(self, host, scope=None, source=None, tags=None) -> bool:
        """Discover / refresh a host. Returns True if newly discovered."""
        return self._upsert_host(host, scope=scope, source=source, tags=tags, tool=source)

    # -- lifecycle: probe -----------------------------------------------------

    def add_probe(self, p: dict) -> None:
        """Upsert one probe result (Vedette's JSON shape), advance state to
        'probed', stamp last_probed_at, and log a probe scan."""
        host = p.get("host") or p.get("input")
        if not host:
            raise ValueError("probe needs a 'host' (or 'input')")
        self._upsert_host(host, scope=p.get("scope"), source=p.get("source"), tool="vedette")
        self.conn.execute(
            """
            INSERT INTO probes (host, url, scheme, port, status, title, server,
                content_type, content_length, ips, tech, body_sha256,
                response_time_ms, ok, error, probed_at, updated_at)
            VALUES (%(host)s, %(url)s, %(scheme)s, %(port)s, %(status)s, %(title)s,
                %(server)s, %(content_type)s, %(content_length)s, %(ips)s, %(tech)s,
                %(body_sha256)s, %(response_time_ms)s, %(ok)s, %(error)s, %(probed_at)s, now())
            ON CONFLICT (host, scheme, port) DO UPDATE SET
                url=EXCLUDED.url, status=EXCLUDED.status, title=EXCLUDED.title,
                server=EXCLUDED.server, content_type=EXCLUDED.content_type,
                content_length=EXCLUDED.content_length, ips=EXCLUDED.ips,
                tech=EXCLUDED.tech, body_sha256=EXCLUDED.body_sha256,
                response_time_ms=EXCLUDED.response_time_ms, ok=EXCLUDED.ok,
                error=EXCLUDED.error, probed_at=EXCLUDED.probed_at, updated_at=now()
            """,
            {
                "host": host, "url": p.get("url"), "scheme": p.get("scheme") or "",
                "port": int(p.get("port") or 0), "status": p.get("status"),
                "title": p.get("title"), "server": p.get("server"),
                "content_type": p.get("content_type"), "content_length": p.get("content_length"),
                "ips": list(p.get("ips") or []), "tech": list(p.get("tech") or []),
                "body_sha256": p.get("body_sha256"), "response_time_ms": p.get("response_time_ms"),
                "ok": p.get("ok"), "error": p.get("error"),
                "probed_at": _parse_ts(p.get("timestamp") or p.get("probed_at")),
            },
        )
        self.conn.execute(
            """
            UPDATE hosts SET
                last_probed_at = now(), last_seen = now(),
                state = CASE WHEN state = 'reviewed' THEN 'reviewed' ELSE 'probed' END
            WHERE host = %s
            """,
            (host,),
        )
        self.log_scan(host, "probe", tool="vedette", ok=p.get("ok"),
                      detail={"status": p.get("status"), "scheme": p.get("scheme"),
                              "port": p.get("port"), "error": p.get("error")})

    # -- lifecycle: review ----------------------------------------------------

    def _mark_reviewed(self, host: str) -> None:
        self.conn.execute(
            "UPDATE hosts SET state='reviewed', last_reviewed_at=now(), last_seen=now() WHERE host=%s",
            (host,),
        )

    def review(self, host: str, tool: str = "aplomado", ok: bool | None = None,
               detail: dict | None = None) -> None:
        """Record that a host was AI-reviewed (advance state, stamp, log) even
        when no finding came of it."""
        self._upsert_host(host, tool=tool)
        self._mark_reviewed(host)
        self.log_scan(host, "review", tool=tool, ok=ok, detail=detail or {})

    def add_finding(self, title, host=None, severity=None, source=None,
                    description=None, data=None) -> int:
        row = self.conn.execute(
            """
            INSERT INTO findings (host, source, severity, title, description, data)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (host, source, severity, title, description, Jsonb(data or {})),
        ).fetchone()
        fid = int(row["id"])
        if host:
            self._upsert_host(host, source=source, tool=source or "aplomado")
            self._mark_reviewed(host)
            self.log_scan(host, "review", tool=source or "aplomado",
                          detail={"finding_id": fid, "severity": severity, "title": title})
        return fid

    # -- ingest ---------------------------------------------------------------

    def ingest_vedette(self, lines: Iterable[str]) -> dict:
        n, ok, bad = 0, 0, 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            self.add_probe(rec)
            n += 1
            if rec.get("ok"):
                ok += 1
        return {"probes": n, "responded": ok, "bad_lines": bad}

    def ingest_foretop(self, lines: Iterable[str]) -> dict:
        n, new, bad = 0, 0, 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            host = rec.get("host")
            if not host:
                bad += 1
                continue
            if self.add_host(host, scope=rec.get("scope"), source=rec.get("source")):
                new += 1
            n += 1
        return {"hosts": n, "new": new, "bad_lines": bad}

    # -- queries --------------------------------------------------------------

    def query_hosts(self, scope=None, state=None, tech=None, status=None,
                    search=None, source=None, limit=100) -> list[dict]:
        where, params = [], []
        base = "SELECT h.* FROM hosts h"
        if tech is not None or status is not None:
            base += " JOIN probes p ON p.host = h.host"
        if scope is not None:
            where.append("h.scope = %s"); params.append(scope)
        if state is not None:
            where.append("h.state = %s"); params.append(state)
        if source is not None:
            where.append("h.source = %s"); params.append(source)
        if search is not None:
            where.append("h.host ILIKE %s"); params.append(f"%{search}%")
        if tech is not None:
            where.append("%s = ANY(p.tech)"); params.append(tech)
        if status is not None:
            where.append("p.status = %s"); params.append(int(status))
        sql = base + (" WHERE " + " AND ".join(where) if where else "")
        sql += " GROUP BY h.id ORDER BY h.last_seen DESC LIMIT %s"
        params.append(int(limit))
        return self.conn.execute(sql, params).fetchall()

    def query_probes(self, host=None, status=None, tech=None, only_ok=None,
                     search=None, limit=100) -> list[dict]:
        where, params = [], []
        if host is not None:
            where.append("host = %s"); params.append(host)
        if status is not None:
            where.append("status = %s"); params.append(int(status))
        if tech is not None:
            where.append("%s = ANY(tech)"); params.append(tech)
        if only_ok is not None:
            where.append("ok = %s"); params.append(bool(only_ok))
        if search is not None:
            where.append("(host ILIKE %s OR title ILIKE %s)")
            params += [f"%{search}%", f"%{search}%"]
        sql = "SELECT * FROM probes"
        sql += (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY updated_at DESC LIMIT %s"
        params.append(int(limit))
        return self.conn.execute(sql, params).fetchall()

    def query_findings(self, host=None, severity=None, source=None, limit=100) -> list[dict]:
        where, params = [], []
        if host is not None:
            where.append("host = %s"); params.append(host)
        if severity is not None:
            where.append("severity = %s"); params.append(severity)
        if source is not None:
            where.append("source = %s"); params.append(source)
        sql = "SELECT * FROM findings"
        sql += (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY found_at DESC LIMIT %s"
        params.append(int(limit))
        return self.conn.execute(sql, params).fetchall()

    def query_scans(self, host=None, kind=None, tool=None, limit=100) -> list[dict]:
        where, params = [], []
        if host is not None:
            where.append("host = %s"); params.append(host)
        if kind is not None:
            where.append("kind = %s"); params.append(kind)
        if tool is not None:
            where.append("tool = %s"); params.append(tool)
        sql = "SELECT * FROM scans"
        sql += (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY scanned_at DESC LIMIT %s"
        params.append(int(limit))
        return self.conn.execute(sql, params).fetchall()

    def host_detail(self, host: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM hosts WHERE host = %s", (host,)).fetchone()
        if row is None:
            return None
        return {
            "host": row,
            "probes": self.query_probes(host=host, limit=50),
            "findings": self.query_findings(host=host, limit=50),
            "scans": self.query_scans(host=host, limit=20),
        }

    def stats(self) -> dict:
        row = self.conn.execute(
            """
            SELECT
              (SELECT count(*) FROM hosts)                                   AS hosts,
              (SELECT count(*) FROM hosts WHERE state='discovered')          AS discovered,
              (SELECT count(*) FROM hosts WHERE state='probed')              AS probed,
              (SELECT count(*) FROM hosts WHERE state='reviewed')            AS reviewed,
              (SELECT count(*) FROM probes)                                  AS probes,
              (SELECT count(*) FROM probes WHERE ok)                         AS responded,
              (SELECT count(*) FROM findings)                                AS findings,
              (SELECT count(*) FROM scans)                                   AS scans,
              (SELECT count(DISTINCT scope) FROM hosts WHERE scope IS NOT NULL) AS scopes
            """
        ).fetchone()
        return dict(row)

    def max_id(self, table: str) -> int:
        if table not in ("probes", "scans", "findings", "hosts"):
            raise ValueError("bad table")
        row = self.conn.execute(f"SELECT COALESCE(MAX(id), 0) AS m FROM {table}").fetchone()
        return int(row["m"])

    def rows_after(self, table: str, after_id: int, limit: int = 1000) -> list[dict]:
        """Rows with id greater than ``after_id``, oldest first. For tailing."""
        if table not in ("probes", "scans", "findings", "hosts"):
            raise ValueError("bad table")
        return self.conn.execute(
            f"SELECT * FROM {table} WHERE id > %s ORDER BY id ASC LIMIT %s",
            (after_id, limit),
        ).fetchall()

    def read_sql(self, query: str, limit: int = 500) -> list[dict]:
        q = query.strip().rstrip(";")
        low = q.lower()
        if not (low.startswith("select") or low.startswith("with")):
            raise ValueError("only SELECT / WITH queries are allowed")
        forbidden = ("insert ", "update ", "delete ", "drop ", "alter ",
                     "truncate ", "create ", "grant ", "revoke ", ";")
        if any(tok in low for tok in forbidden):
            raise ValueError("query contains a disallowed statement")
        if " limit " not in low:
            q += f" LIMIT {int(limit)}"
        return self.conn.execute(q).fetchall()
