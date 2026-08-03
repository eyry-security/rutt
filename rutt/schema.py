"""The recon data model.

Rutt is host-centric. A host has a **lifecycle**: it is *discovered*, then
*probed*, then *reviewed*. The ``hosts`` table tracks how far along each host is
and when each stage last happened; every stage also writes a row to ``scans`` so
there is a full, queryable history of what touched a host and when.

Tables:

* ``hosts``    — one row per hostname, with lifecycle state and timestamps
                 (``first_seen``, ``last_seen``, ``last_probed_at``, ``last_reviewed_at``)
* ``probes``   — current probe result per (host, scheme, port), upserted (Vedette)
* ``findings`` — reportable findings (Aplomado / by hand)
* ``scans``    — an append-only log of every scan event (discover / probe / review)

State advances only, never regresses: re-probing a reviewed host keeps it
``reviewed``. The DDL is idempotent, so ``rutt init`` is safe to re-run.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (
    id               BIGSERIAL PRIMARY KEY,
    host             TEXT UNIQUE NOT NULL,
    scope            TEXT,
    source           TEXT,
    tags             TEXT[] NOT NULL DEFAULT '{}',
    state            TEXT NOT NULL DEFAULT 'discovered',
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_probed_at   TIMESTAMPTZ,
    last_reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS hosts_scope_idx     ON hosts (scope);
CREATE INDEX IF NOT EXISTS hosts_state_idx     ON hosts (state);
CREATE INDEX IF NOT EXISTS hosts_last_seen_idx ON hosts (last_seen DESC);

CREATE TABLE IF NOT EXISTS probes (
    id               BIGSERIAL PRIMARY KEY,
    host             TEXT NOT NULL,
    url              TEXT,
    scheme           TEXT NOT NULL DEFAULT '',
    port             INTEGER NOT NULL DEFAULT 0,
    status           INTEGER,
    title            TEXT,
    server           TEXT,
    content_type     TEXT,
    content_length   BIGINT,
    ips              TEXT[] NOT NULL DEFAULT '{}',
    tech             TEXT[] NOT NULL DEFAULT '{}',
    body_sha256      TEXT,
    response_time_ms INTEGER,
    ok               BOOLEAN,
    error            TEXT,
    first_probed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    probed_at        TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (host, scheme, port)
);
CREATE INDEX IF NOT EXISTS probes_host_idx   ON probes (host);
CREATE INDEX IF NOT EXISTS probes_status_idx ON probes (status);
CREATE INDEX IF NOT EXISTS probes_tech_idx   ON probes USING GIN (tech);

CREATE TABLE IF NOT EXISTS findings (
    id          BIGSERIAL PRIMARY KEY,
    host        TEXT,
    source      TEXT,
    severity    TEXT,
    title       TEXT NOT NULL,
    description TEXT,
    data        JSONB NOT NULL DEFAULT '{}',
    found_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS findings_host_idx     ON findings (host);
CREATE INDEX IF NOT EXISTS findings_severity_idx ON findings (severity);

CREATE TABLE IF NOT EXISTS scans (
    id         BIGSERIAL PRIMARY KEY,
    host       TEXT,
    kind       TEXT NOT NULL,          -- discover | probe | review
    tool       TEXT,                   -- foretop | vedette | aplomado | ...
    ok         BOOLEAN,
    detail     JSONB NOT NULL DEFAULT '{}',
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scans_host_idx       ON scans (host);
CREATE INDEX IF NOT EXISTS scans_kind_idx       ON scans (kind);
CREATE INDEX IF NOT EXISTS scans_scanned_at_idx ON scans (scanned_at DESC);
"""
