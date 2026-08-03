# Rutt

A Postgres-backed store for recon data, with a CLI and a library. The logbook of
the [Eyry](https://eyry.io) recon suite.

> A *rutter* was a mariner's logbook — the accumulated written record of a
> voyage: every coast, hazard, and landmark seen along the way. Rutt is that for
> a target: every host seen, every probe result, every finding, kept in one
> place you can add to and query.

Rutt is the memory of the pipeline. [Foretop](https://github.com/eyry-security/foretop)
finds hosts and [Vedette](https://github.com/eyry-security/vedette) probes them;
Rutt is where those results live so you can ask "what's in scope, running nginx,
and returned a 200?" months later.

## The host lifecycle

Every host moves through a lifecycle, and Rutt tracks how far along it is and
when each stage last happened:

```
discovered  ──probe──►  probed  ──review──►  reviewed
(Foretop)               (Vedette)            (Aplomado)
```

- A host is **discovered** the first time it's seen (from Foretop, or the first
  time anything touches it). `first_seen` is stamped.
- When it's **probed**, its probe result is upserted and `last_probed_at` moves.
- When it's **reviewed** by the AI scanner, `last_reviewed_at` moves.

State only advances — re-probing a reviewed host keeps it `reviewed`. Every
stage also appends to a `scans` log, so "what touched this host, and when" is
always answerable (`rutt scans`, `rutt host <host>`).

MIT licensed.

## Install

```sh
git clone https://github.com/eyry-security/rutt
cd rutt
pip install -e .
```

Requires Python 3.9+ and a reachable Postgres. Point Rutt at it with `--dsn`, or
set `RUTT_DSN` / `DATABASE_URL`:

```sh
export RUTT_DSN=postgresql://user:pass@localhost:5432/rutt
rutt init          # create the schema (safe to re-run)
```

## Use it in the pipeline

Vedette writes one JSON object per host; pipe that straight into Rutt:

```sh
vedette -l hosts.txt -o - | rutt ingest vedette -
foretop --scope '*.example.com' | rutt ingest foretop -
```

Then query the accumulated record:

```sh
rutt hosts --scope '*.example.com'
rutt probes --status 200 --tech nginx
rutt findings --severity high
rutt stats
```

## CLI

| Command | What it does |
| --- | --- |
| `rutt init` | Create the schema (idempotent) |
| `rutt add host <host>` | Discover/refresh a host (`--scope --source --tag`) |
| `rutt add finding <title>` | Record a finding — advances the host to `reviewed` |
| `rutt ingest vedette [file]` | Load Vedette JSONL into `probes` (advances to `probed`) |
| `rutt ingest foretop [file]` | Load Foretop JSONL into `hosts` (discovery) |
| `rutt review <host>` | Mark a host AI-reviewed (`--tool --note`) |
| `rutt hosts` | Query hosts (`--scope --state --source --search --tech --status`) |
| `rutt host <host>` | Full lifecycle detail: state, timestamps, probes, findings, scans |
| `rutt probes` | Query probes (`--host --status --tech --ok --search`) |
| `rutt findings` | Query findings (`--host --severity --source`) |
| `rutt scans` | The scan log (`--host --kind discover\|probe\|review --tool`) |
| `rutt sql "<SELECT>"` | Run a read-only query (SELECT/WITH only) |
| `rutt stats` | Row counts + lifecycle breakdown |

Every query takes `--limit` and `--json` (JSONL out; the default is a table).

## Library

```python
from rutt import Rutt

with Rutt("postgresql:///rutt") as r:
    r.init_schema()
    r.add_host("api.example.com", scope="*.example.com", source="certstream")  # discovered
    r.add_probe({"host": "api.example.com", "scheme": "https", "port": 443,     # -> probed
                 "status": 200, "server": "nginx", "tech": ["nginx"], "ok": True})
    r.add_finding("Exposed .git", host="api.example.com", severity="high")      # -> reviewed
    # ...or record a clean review with no finding:
    r.review("api.example.com", tool="aplomado", ok=True)

    for row in r.query_probes(status=200, tech="nginx"):
        print(row["host"], row["title"])

    print(r.host_detail("api.example.com"))   # state, timestamps, probes, findings, scans
```

`add_probe` takes Vedette's JSON shape directly, so an ingesting worker is a
one-liner — and it advances the host's state, stamps `last_probed_at`, and logs
a probe scan for you. Findings take arbitrary structured `data` (JSONB).

## Schema

| Table | One row per | Filled by |
| --- | --- | --- |
| `hosts` | hostname ever seen — with `state`, `first_seen`, `last_seen`, `last_probed_at`, `last_reviewed_at` | Foretop, or anything |
| `probes` | probed endpoint `(host, scheme, port)`, upserted to stay current | Vedette |
| `findings` | reportable finding | Aplomado, or by hand |
| `scans` | every scan event (`discover` / `probe` / `review`), append-only | all stages |

`probes.tech` and `probes.ips` are Postgres arrays (GIN-indexed on `tech`);
`findings.data` and `scans.detail` are JSONB. Run `rutt init` to create it all.

## Where it fits

```
Foretop → Purser → Vedette ─┐
                            ├─► Rutt (Postgres)  ◄── Aplomado findings
        queries / reports ◄─┘
```

Rutt is the shared store the whole suite reads from and writes to. See the suite
at [github.com/eyry-security](https://github.com/eyry-security).

## Roadmap

- Migrations (versioned schema changes)
- `services`/ports beyond 80/443, and historical probe history (not just current)
- Full-text search over titles and findings
- Export to CSV / SARIF

## License

MIT © Eyry
