# Rutt

A Postgres store for recon data, with a CLI and a library.
Part of [Eyry](https://eyry.io).

A *rutter* was a mariner's logbook — the accumulated written record of a
voyage: every coast, hazard, and landmark seen along the way. Rutt is that for
a target: every host seen, every probe result, every finding, in one place you
can add to and query.

## What it does

- Tracks every host through a lifecycle: **discovered → probed → reviewed**.
- Stores full probe results (status, server, title, tech stack, IPs, TLS),
  upserted so each host's record stays current.
- Keeps an append-only **scan log** of everything that touched every host —
  "what discovered this, when was it last probed, who reviewed it" is always
  answerable.
- Queryable from the CLI (`rutt hosts --tech nginx --status 200`) or raw SQL
  (`rutt sql`, read-only).
- Streams new rows as they arrive (`rutt tail`) so downstream tools can react
  to live pipeline output.

## Install

```sh
git clone https://github.com/eyry-security/rutt
cd rutt
pip install -e .
```

Requires Python 3.9+ and a Postgres you can reach. Point Rutt at it with
`--dsn`, or set `RUTT_DSN` / `DATABASE_URL`:

```sh
export RUTT_DSN=postgresql://user:pass@localhost:5432/rutt
rutt init          # create the schema (idempotent — safe to re-run)
```

## Quickstart

Vedette writes one JSON object per host; Foretop writes new hosts. Pipe both
straight in:

```sh
# ingest discovery + probing
foretop --scope '*.example.com' --redis redis://127.0.0.1:6379 --queue purser:in &
purser ingest --from purser:in --tier warm &
purser feed --to vedette:hosts &
vedette --redis redis://127.0.0.1:6379 --queue vedette:hosts -o - | rutt ingest vedette -
```

Then ask the accumulated record questions:

```sh
$ rutt hosts --scope '*.example.com'
host             state      scope            source      last_probed_at       last_reviewed_at  first_seen
--               --         --               --          --                   --                --
api.example.com  reviewed   *.example.com    certstream  2026-10-04 08:12:01  2026-10-04 09:01:44  2026-10-04 07:58:20
blog.example.com probed     *.example.com    certstream  2026-10-04 08:12:03  -                 2026-10-04 07:58:22

2 row(s)

$ rutt probes --status 200 --tech nginx --limit 5
$ rutt findings --severity high
$ rutt stats
hosts=214 (discovered=96 probed=101 reviewed=17)  probes=230 (responded=198)  findings=3  scans=431  scopes=2
```

Or watch the pipeline stream in live:

```sh
$ rutt tail              # new probe results, one JSON object per line
{"id":231,"host":"cdn-7.example.com","scheme":"https","port":443,"status":200,"title":"Example CDN","server":"nginx","content_type":"text/html","content_length":15321,"ips":["93.184.216.34"],"tech":["nginx"],"body_sha256":"a94f…","response_time_ms":212,"ok":true,"error":null,"first_probed_at":"2026-10-04T08:14:02+00:00","probed_at":"2026-10-04T08:14:02+00:00","updated_at":"2026-10-04T08:14:02+00:00"}

$ rutt tail --scans       # or the raw discover/probe/review event log
{"id":432,"host":"cdn-7.example.com","kind":"probe","tool":"vedette","ok":true,"detail":{"status":200,"scheme":"https","port":443,"error":null},"scanned_at":"2026-10-04T08:14:02+00:00"}
```

## The host lifecycle

```
discovered  ──probe──►  probed  ──review──►  reviewed
(Foretop)               (Vedette)            (Aplomado)
```

- A host is **discovered** the first time it's seen. `first_seen` is stamped.
- When it's **probed**, the probe result is upserted and `last_probed_at` moves.
- When it's **reviewed** (Aplomado, or by hand), `last_reviewed_at` moves.

State only advances — re-probing a reviewed host keeps it `reviewed`. Every
stage also appends to the `scans` log, so the full touch history is one query
away:

```sh
$ rutt scans --host api.example.com --limit 3
scanned_at           kind      tool       host             ok    detail
--                   --        --         --               --    --
2026-10-04 09:01:44  review    aplomado   api.example.com  true  {"note":"exposed .git reviewed"}
2026-10-04 08:12:01  probe     vedette    api.example.com  true  {"status":200,"scheme":"https","port":443,"error":null}
2026-10-04 07:58:20  discover  foretop    api.example.com  -     {"scope":"*.example.com"}

3 row(s)

$ rutt host api.example.com
api.example.com   [reviewed]
  scope:        *.example.com
  source:       certstream
  tags:         -
  first_seen:   2026-10-04 07:58:20+00:00
  last_seen:    2026-10-04 09:01:44+00:00
  last_probed:  2026-10-04 08:12:01+00:00
  last_reviewed:2026-10-04 09:01:44+00:00

  probes (2):
  scheme  port  status  title        server  tech     updated_at
  --      --    --      --           --      --       --
  https   443   200     Example API  nginx   [nginx]  2026-10-04 08:12:01

  findings (1):
  id  severity  title          source    found_at
  --  --        --             --        --
  1   high      Exposed .git   aplomado  2026-10-04 09:01:44

  recent scans (3):
  kind      tool      ok    scanned_at
  --        --        --    --
  review    aplomado  true  2026-10-04 09:01:44
  probe     vedette   true  2026-10-04 08:12:01
  discover  foretop   -     2026-10-04 07:58:20
```

## CLI

| Command | What it does |
| --- | --- |
| `rutt init` | Create the schema (idempotent) |
| `rutt add host <host>` | Discover/refresh a host (`--scope --source --tag`, repeatable) |
| `rutt add finding <title>` | Record a finding (`--host --severity --source --desc --data`); advances the host to `reviewed` |
| `rutt ingest vedette [file]` | Load Vedette JSONL into `probes` (advances to `probed`); `-` or omitted = stdin |
| `rutt ingest foretop [file]` | Load Foretop JSONL into `hosts` (discovery) |
| `rutt review <host>` | Mark a host AI-reviewed (`--tool`, default `aplomado`; `--note`) |
| `rutt hosts` | Query hosts (`--scope --source --search --state --tech --status`) |
| `rutt host <host>` | Full lifecycle detail: state, timestamps, probes, findings, scans |
| `rutt probes` | Query probes (`--host --status --tech --ok --search`) |
| `rutt findings` | Query findings (`--host --severity --source`) |
| `rutt scans` | The scan log (`--host --kind discover\|probe\|review --tool`) |
| `rutt sql "<SELECT>"` | Run a read-only query (SELECT/WITH only) |
| `rutt tail` | Stream new probes as JSON, like `tail -f` (`--scans`, `--all`, `--interval`) |
| `rutt stats` | Row counts + lifecycle breakdown |

Every query takes `--limit` (default 100) and `--json` (JSONL out; default is a
table).

## Library

```python
from rutt import Rutt

with Rutt("postgresql:///rutt") as r:
    r.init_schema()

    # discovery (advances nothing — this is the start of the lifecycle)
    r.add_host("api.example.com", scope="*.example.com", source="certstream")

    # a probe result — takes Vedette's JSON shape directly,
    # advances the host to 'probed', stamps last_probed_at, logs a scan
    r.add_probe({"host": "api.example.com", "scheme": "https", "port": 443,
                 "status": 200, "server": "nginx", "tech": ["nginx"], "ok": True})

    # a finding (advances to 'reviewed') — arbitrary structured data welcome
    r.add_finding("Exposed .git", host="api.example.com", severity="high",
                  source="aplomado", data={"path": "/.git/HEAD"})
    # ...or a clean review with no finding:
    r.review("blog.example.com", tool="aplomado", ok=True)

    for row in r.query_probes(status=200, tech="nginx"):
        print(row["host"], row["title"])

    print(r.host_detail("api.example.com"))  # state, timestamps, probes, findings, scans
```

`add_probe` is the one-liner that makes an ingesting worker trivial: it
upserts the probe, advances state, stamps the clock, and logs the scan.

## Schema

| Table | One row per | Filled by |
| --- | --- | --- |
| `hosts` | hostname ever seen — with `state`, `first_seen`, `last_seen`, `last_probed_at`, `last_reviewed_at` | Foretop, or anything |
| `probes` | probed endpoint `(host, scheme, port)`, upserted to stay current | Vedette |
| `findings` | reportable finding | Aplomado, or by hand |
| `scans` | every scan event (`discover` / `probe` / `review`), append-only | all stages |

`probes.tech` and `probes.ips` are Postgres arrays (`tech` is GIN-indexed, so
`--tech nginx` is fast); `findings.data` and `scans.detail` are JSONB.

## Where it fits

`Foretop (new hosts) → Purser (queue) → Vedette (probe) → Rutt (store) → Aplomado (AI review)`

Rutt is the suite's shared memory: the data plane writes to it and the agent
plane reads from it. Months later, you can still ask "what was in scope,
running nginx, and returning a 200?"

## The Eyry suite

- **eyry**: one CLI that wires the data plane together — discover → queue → probe → store
- **vedette**: fast, multi-threaded HTTP prober (Rust) — confirms what is live and fingerprints it
- **foretop**: pluggable live feed of new hosts, starting with Certificate Transparency logs
- **purser**: Redis-backed priority work queue — hot/warm/cold lanes, retries, dead-letter queue
- **rutt**: Postgres store for the host lifecycle (discovered → probed → reviewed) with an append-only scan log
- **pinnace**: general multi-turn agent runtime — compaction, tools, Docker sandbox, resumable sessions
- **aplomado**: AI security reviewer built on Pinnace — target in, structured findings out
- **quarterdeck**: agent control plane — scheduler, wake/sleep, identity and memory, IRC-style chat, ChatOps, pipeline orchestration
## Roadmap

- Versioned schema migrations (currently `rutt init` creates everything at once)
- Historical probe history (currently only the current probe per host is kept)
- Full-text search over titles and findings
- Export to CSV / SARIF

## License

MIT © Eyry
