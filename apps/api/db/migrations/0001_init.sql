-- ControlPlane.ai V1 schema (Neon / Postgres)
-- Three tables: policy_layers (human-authored inputs), bundles (compiled output),
-- ledger (hash-chained record of every decision). No tables for learning-plane
-- features (reviewer queue, shadow eval) — out of scope per .agents/context.md.

CREATE TABLE IF NOT EXISTS policy_layers (
    id          BIGSERIAL PRIMARY KEY,
    layer_type  TEXT NOT NULL CHECK (layer_type IN ('org', 'tenant')),
    name        TEXT NOT NULL,               -- e.g. 'org-baseline', 'support-bot'
    version     INTEGER NOT NULL,
    content     JSONB NOT NULL,               -- parsed YAML: fields + locks[], as authored
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (layer_type, name, version)
);

CREATE TABLE IF NOT EXISTS bundles (
    id            BIGSERIAL PRIMARY KEY,
    policy_name   TEXT NOT NULL,
    version       INTEGER NOT NULL,
    hash          TEXT NOT NULL,              -- sha256(json.dumps(fields, sort_keys=True)), fields-only per PRD §3.4
    source_layers JSONB NOT NULL,             -- e.g. ["org-baseline", "tenant-support-bot"]
    fields        JSONB NOT NULL,             -- resolved bundle fields (the data plane's actual input)
    clamp_events  JSONB NOT NULL DEFAULT '[]', -- resolve()'s override/clamp log, PRD §3.4.1
    compiled_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (policy_name, version)
);

-- Hash-chained, append-only. prev_hash/hash link every row into one global chain
-- (chain order = id, which is monotonic) so tampering with any row breaks every
-- hash after it. Input and output stages are deliberately different shapes
-- (context.md: "Two row shapes — don't unify them") — enforced below via CHECK
-- rather than left to application-level convention.
CREATE TABLE IF NOT EXISTS ledger (
    id                   BIGSERIAL PRIMARY KEY,
    request_id           UUID NOT NULL,
    stage                TEXT NOT NULL CHECK (stage IN ('input', 'output')),
    bundle_id            BIGINT NOT NULL REFERENCES bundles(id),
    tier_reached         TEXT CHECK (tier_reached IN ('t0', 't1')),  -- output only
    action               TEXT NOT NULL,
    review_needed        BOOLEAN,             -- input stage only
    modifiers            JSONB,               -- output stage only: array of "redact"/"flag_visible"/"flag"
    contributing_signals JSONB NOT NULL DEFAULT '[]',  -- [{source, type, score, status}] — types/scores only, never raw values
    prev_hash            TEXT,                -- null only for the very first row in the chain
    hash                 TEXT NOT NULL,       -- sha256(json.dumps(record, sort_keys=True)) over prev_hash + this row's contents
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (
        (stage = 'input'  AND tier_reached IS NULL AND modifiers IS NULL AND review_needed IS NOT NULL)
        OR
        (stage = 'output' AND review_needed IS NULL AND modifiers IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_ledger_request_id ON ledger (request_id);
CREATE INDEX IF NOT EXISTS idx_ledger_created_at ON ledger (created_at);
CREATE INDEX IF NOT EXISTS idx_bundles_policy_name ON bundles (policy_name);
