"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import __version__
from .store import Rutt, default_dsn


def _add_dsn(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--dsn", default=None,
                    help="Postgres DSN (default: $RUTT_DSN, $DATABASE_URL, else postgresql:///rutt)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rutt",
        description="A Postgres-backed store for recon data. Add to it and query it.",
    )
    p.add_argument("--version", action="version", version=f"rutt {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create the schema (idempotent)")
    _add_dsn(sp)

    sp = sub.add_parser("stats", help="row counts")
    _add_dsn(sp); sp.add_argument("--json", action="store_true")

    # add
    sp = sub.add_parser("add", help="add a host or finding")
    add_sub = sp.add_subparsers(dest="what", required=True)

    ah = add_sub.add_parser("host", help="add/refresh a host")
    _add_dsn(ah)
    ah.add_argument("host")
    ah.add_argument("--scope"); ah.add_argument("--source")
    ah.add_argument("--tag", action="append", default=[], dest="tags")

    af = add_sub.add_parser("finding", help="record a finding")
    _add_dsn(af)
    af.add_argument("title")
    af.add_argument("--host"); af.add_argument("--severity"); af.add_argument("--source")
    af.add_argument("--desc", dest="description")
    af.add_argument("--data", help="JSON object of extra fields")

    # ingest
    sp = sub.add_parser("ingest", help="load JSONL from Vedette or Foretop")
    ing_sub = sp.add_subparsers(dest="kind", required=True)
    for kind in ("vedette", "foretop"):
        isp = ing_sub.add_parser(kind, help=f"ingest {kind} JSONL")
        _add_dsn(isp)
        isp.add_argument("file", nargs="?", default="-", help="input file (default: stdin)")

    # queries
    sp = sub.add_parser("hosts", help="query hosts")
    _add_dsn(sp)
    sp.add_argument("--scope"); sp.add_argument("--source"); sp.add_argument("--search")
    sp.add_argument("--state", choices=["discovered", "probed", "reviewed"])
    sp.add_argument("--tech"); sp.add_argument("--status", type=int)
    sp.add_argument("--limit", type=int, default=100); sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("host", help="full lifecycle detail for one host")
    _add_dsn(sp); sp.add_argument("host"); sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("scans", help="the scan log (discover / probe / review)")
    _add_dsn(sp)
    sp.add_argument("--host"); sp.add_argument("--kind", choices=["discover", "probe", "review"])
    sp.add_argument("--tool"); sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("review", help="mark a host AI-reviewed (advance state, log it)")
    _add_dsn(sp); sp.add_argument("host")
    sp.add_argument("--tool", default="aplomado"); sp.add_argument("--note")

    sp = sub.add_parser("probes", help="query probes")
    _add_dsn(sp)
    sp.add_argument("--host"); sp.add_argument("--status", type=int); sp.add_argument("--tech")
    sp.add_argument("--ok", dest="only_ok", action="store_true")
    sp.add_argument("--search"); sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("findings", help="query findings")
    _add_dsn(sp)
    sp.add_argument("--host"); sp.add_argument("--severity"); sp.add_argument("--source")
    sp.add_argument("--limit", type=int, default=100); sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("sql", help="run a read-only SELECT")
    _add_dsn(sp)
    sp.add_argument("query"); sp.add_argument("--json", action="store_true")

    return p


def _open(args) -> Rutt:
    return Rutt(dsn=getattr(args, "dsn", None))


def _serial(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _emit(rows: list[dict], as_json: bool, columns=None) -> None:
    if as_json:
        for r in rows:
            print(json.dumps({k: _serial(v) for k, v in r.items()}, ensure_ascii=False))
        return
    if not rows:
        print("(no rows)")
        return
    cols = columns or list(rows[0].keys())
    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, list):
            return ",".join(map(str, v))
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M")
        return str(v)
    widths = {c: len(c) for c in cols}
    table = []
    for r in rows:
        row = {c: fmt(r.get(c)) for c in cols}
        for c in cols:
            widths[c] = min(max(widths[c], len(row[c])), 48)
        table.append(row)
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("  ".join("-" * widths[c] for c in cols))
    for row in table:
        print("  ".join(row[c][:widths[c]].ljust(widths[c]) for c in cols))
    print(f"\n{len(rows)} row(s)")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            with _open(args) as q:
                q.init_schema()
            print(f"[rutt] schema ready at {args.dsn or default_dsn()}")
            return 0

        if args.cmd == "stats":
            with _open(args) as q:
                s = q.stats()
            if args.json:
                print(json.dumps(s))
            else:
                print(f"hosts={s['hosts']} (discovered={s['discovered']} probed={s['probed']} "
                      f"reviewed={s['reviewed']})  probes={s['probes']} (responded={s['responded']})  "
                      f"findings={s['findings']}  scans={s['scans']}  scopes={s['scopes']}")
            return 0

        if args.cmd == "add" and args.what == "host":
            with _open(args) as q:
                q.add_host(args.host, scope=args.scope, source=args.source, tags=args.tags)
            print(f"[rutt] host {args.host}")
            return 0

        if args.cmd == "add" and args.what == "finding":
            data = json.loads(args.data) if args.data else {}
            with _open(args) as q:
                fid = q.add_finding(args.title, host=args.host, severity=args.severity,
                                    source=args.source, description=args.description, data=data)
            print(f"[rutt] finding #{fid}")
            return 0

        if args.cmd == "ingest":
            stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
            try:
                with _open(args) as q:
                    result = (q.ingest_vedette if args.kind == "vedette" else q.ingest_foretop)(stream)
            finally:
                if stream is not sys.stdin:
                    stream.close()
            print(f"[rutt] {args.kind}: {result}")
            return 0

        if args.cmd == "hosts":
            with _open(args) as q:
                rows = q.query_hosts(scope=args.scope, state=args.state, source=args.source,
                                     search=args.search, tech=args.tech, status=args.status,
                                     limit=args.limit)
            _emit(rows, args.json,
                  columns=["host", "state", "scope", "source", "last_probed_at",
                           "last_reviewed_at", "first_seen"])
            return 0

        if args.cmd == "host":
            with _open(args) as q:
                detail = q.host_detail(args.host)
            if detail is None:
                print(f"rutt: no such host: {args.host}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(detail, default=_serial, ensure_ascii=False))
                return 0
            h = detail["host"]
            print(f"{h['host']}   [{h['state']}]")
            print(f"  scope:        {h['scope'] or '-'}")
            print(f"  source:       {h['source'] or '-'}")
            print(f"  tags:         {','.join(h['tags']) or '-'}")
            print(f"  first_seen:   {h['first_seen']}")
            print(f"  last_seen:    {h['last_seen']}")
            print(f"  last_probed:  {h['last_probed_at'] or '-'}")
            print(f"  last_reviewed:{h['last_reviewed_at'] or '-'}")
            print(f"\n  probes ({len(detail['probes'])}):")
            _emit(detail["probes"], False,
                  columns=["scheme", "port", "status", "title", "server", "tech", "updated_at"]) \
                if detail["probes"] else print("    (none)")
            print(f"\n  findings ({len(detail['findings'])}):")
            _emit(detail["findings"], False,
                  columns=["id", "severity", "title", "source", "found_at"]) \
                if detail["findings"] else print("    (none)")
            print(f"\n  recent scans ({len(detail['scans'])}):")
            _emit(detail["scans"], False,
                  columns=["kind", "tool", "ok", "scanned_at"]) \
                if detail["scans"] else print("    (none)")
            return 0

        if args.cmd == "scans":
            with _open(args) as q:
                rows = q.query_scans(host=args.host, kind=args.kind, tool=args.tool, limit=args.limit)
            _emit(rows, args.json, columns=["scanned_at", "kind", "tool", "host", "ok", "detail"])
            return 0

        if args.cmd == "review":
            detail = {"note": args.note} if args.note else {}
            with _open(args) as q:
                q.review(args.host, tool=args.tool, detail=detail)
            print(f"[rutt] reviewed {args.host} (tool={args.tool})")
            return 0

        if args.cmd == "probes":
            with _open(args) as q:
                rows = q.query_probes(host=args.host, status=args.status, tech=args.tech,
                                      only_ok=args.only_ok or None, search=args.search, limit=args.limit)
            _emit(rows, args.json,
                  columns=["host", "scheme", "port", "status", "title", "server", "tech", "updated_at"])
            return 0

        if args.cmd == "findings":
            with _open(args) as q:
                rows = q.query_findings(host=args.host, severity=args.severity,
                                        source=args.source, limit=args.limit)
            _emit(rows, args.json, columns=["id", "severity", "host", "title", "source", "found_at"])
            return 0

        if args.cmd == "sql":
            with _open(args) as q:
                rows = q.read_sql(args.query)
            _emit(rows, args.json)
            return 0

    except (ValueError, json.JSONDecodeError) as exc:
        print(f"rutt: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"rutt: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
