-- Learning Plane PRD: the 100-case mock dataset (calibration + FN/FP metrics
-- + test-case browser) is authored against ONE specific canonical policy
-- (input/output PII bands 0.4/0.8, toxicity off, output_pii_redact_threshold
-- locked tighten-only at 0.8). It is only meaningful against an
-- agent/organization actually running that policy — showing it on an
-- arbitrary real agent (e.g. a real "support-bot" with its own thresholds)
-- would be misleading, not just irrelevant.
--
-- Fix: a single, auto-seeded "Demo Org" (organizations.is_demo) carries the
-- canonical policy and one "Demo Agent" under it. The Calibration + Metrics
-- page only ever renders for this org. Real orgs/agents never see it.

ALTER TABLE organizations ADD COLUMN is_demo BOOLEAN NOT NULL DEFAULT false;

-- At most one demo org can ever exist.
CREATE UNIQUE INDEX idx_organizations_one_demo ON organizations (is_demo) WHERE is_demo;
