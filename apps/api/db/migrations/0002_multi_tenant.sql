-- Multi-tenant redesign: organizations sign up/log in, each creates one
-- org-level policy, then adds any number of agents, each with its own
-- agent-level policy layer resolved against the org layer.
--
-- Replaces the V1 single hardcoded org-baseline/support-bot pair with real
-- per-organization scoping. Pre-existing rows in policy_layers/bundles/ledger
-- were throwaway test data from single-tenant development — truncated here
-- rather than migrated, since they carry no organization/agent identity to
-- migrate to.

CREATE TABLE organizations (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agents (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, name)
);

TRUNCATE ledger, bundles, policy_layers RESTART IDENTITY;

ALTER TABLE policy_layers DROP CONSTRAINT policy_layers_layer_type_check;
ALTER TABLE policy_layers DROP CONSTRAINT policy_layers_layer_type_name_version_key;
ALTER TABLE policy_layers DROP COLUMN name;
ALTER TABLE policy_layers ADD COLUMN organization_id BIGINT NOT NULL REFERENCES organizations(id);
ALTER TABLE policy_layers ADD COLUMN agent_id BIGINT REFERENCES agents(id);
ALTER TABLE policy_layers ADD CONSTRAINT policy_layers_layer_type_check
    CHECK (layer_type IN ('org', 'agent'));
ALTER TABLE policy_layers ADD CONSTRAINT policy_layers_agent_id_matches_type
    CHECK (
        (layer_type = 'org' AND agent_id IS NULL) OR
        (layer_type = 'agent' AND agent_id IS NOT NULL)
    );
-- One version sequence per org for its org-layer, one per agent for its
-- agent-layer — partial indexes avoid NULL-uniqueness ambiguity on agent_id.
CREATE UNIQUE INDEX idx_org_layer_version ON policy_layers (organization_id, version) WHERE layer_type = 'org';
CREATE UNIQUE INDEX idx_agent_layer_version ON policy_layers (agent_id, version) WHERE layer_type = 'agent';

ALTER TABLE bundles DROP CONSTRAINT bundles_policy_name_version_key;
ALTER TABLE bundles DROP COLUMN policy_name;
ALTER TABLE bundles ADD COLUMN organization_id BIGINT NOT NULL REFERENCES organizations(id);
ALTER TABLE bundles ADD COLUMN agent_id BIGINT NOT NULL REFERENCES agents(id);
ALTER TABLE bundles ADD CONSTRAINT bundles_agent_id_version_key UNIQUE (agent_id, version);

CREATE INDEX idx_agents_organization_id ON agents (organization_id);
CREATE INDEX idx_bundles_agent_id ON bundles (agent_id);
