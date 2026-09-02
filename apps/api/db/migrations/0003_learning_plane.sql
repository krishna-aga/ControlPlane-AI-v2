-- Learning Plane V1 slice (.agents/Learning_Plane_PRD_Draft.md):
-- multi-version bundle storage + production flag, reviewer-queue verdict
-- fields on the ledger, and two new tables backing the 100-case synthetic
-- eval (FN-rate, metrics, calibration sweep).

-- ---- Multi-version bundle storage (PRD §1.1) ----

ALTER TABLE bundles ADD COLUMN is_production BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE bundles ADD COLUMN label TEXT;

-- Exactly one production bundle per agent at a time.
CREATE UNIQUE INDEX idx_bundles_one_production_per_agent
    ON bundles (agent_id) WHERE is_production;

-- Backfill: every agent's latest existing bundle becomes production, so
-- /check (which now loads the production bundle, not "latest") doesn't go
-- dark for agents that already had a compiled bundle before this migration.
UPDATE bundles b SET is_production = true
WHERE b.version = (SELECT MAX(b2.version) FROM bundles b2 WHERE b2.agent_id = b.agent_id);

-- ---- Reviewer queue verdicts (PRD §2) — a label on the existing ledger
-- row, not a separate table. No downstream wiring: purely for reporting.

ALTER TABLE ledger ADD COLUMN review_verdict TEXT CHECK (review_verdict IN ('approved', 'rejected'));
ALTER TABLE ledger ADD COLUMN reviewed_by TEXT;
ALTER TABLE ledger ADD COLUMN reviewed_at TIMESTAMPTZ;

-- ---- Synthetic eval runs (PRD §3/§4/§5/§6) ----
-- Never stores prompt/response text — same rule as the ledger itself. Ground
-- truth + actual outcome only; the raw per-case score used for FN-rate and
-- the calibration sweep is read back from the ledger row via request_id.

CREATE TABLE learning_eval_runs (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    agent_id        BIGINT NOT NULL REFERENCES agents(id),
    bundle_id       BIGINT NOT NULL REFERENCES bundles(id),
    dataset_name    TEXT NOT NULL,
    case_count      INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE learning_eval_cases (
    id                  BIGSERIAL PRIMARY KEY,
    eval_run_id         BIGINT NOT NULL REFERENCES learning_eval_runs(id),
    case_id             TEXT NOT NULL,
    request_id          UUID NOT NULL,
    category_planted    TEXT NOT NULL,
    ground_truth_risky  BOOLEAN NOT NULL,
    correct_base_action TEXT NOT NULL,
    correct_modifiers   JSONB NOT NULL,
    actual_action       TEXT NOT NULL,
    actual_modifiers    JSONB NOT NULL,
    latency_ms          INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_eval_cases_run_id ON learning_eval_cases (eval_run_id);
CREATE INDEX idx_eval_cases_request_id ON learning_eval_cases (request_id);
