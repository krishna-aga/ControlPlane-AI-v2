"""Auto-seeds the single 'Demo Org' + 'Demo Agent' pair the mock 100-case
calibration dataset is actually valid against. Called once from `main.py`'s
lifespan on every startup; idempotent — does nothing once the demo org
already exists (`organizations.is_demo`, at most one row, migration 0005).

Why this exists: the mock dataset (`learning_plane/mock_eval.py`) is
authored against one specific policy (PII bands 0.4/0.8,
`output_pii_redact_threshold` locked tighten-only at 0.8, toxicity off).
Showing the Calibration + Metrics page against an arbitrary real agent's own
policy would silently misrepresent what those numbers mean. This org exists
so that page always has a policy the dataset is genuinely correct for —
real orgs/agents never get it and never see that page.
"""

import uuid

from auth import hash_password
from control_plane.compile import compile_bundle
from db.queries import create_agent, get_demo_org, insert_agent_layer, insert_bundle, insert_org_layer

DEMO_ORG_NAME = "Demo Org"
DEMO_ORG_EMAIL = "demo@controlplane.ai"
DEMO_AGENT_NAME = "Demo Agent"

# Matches .agents/Learning_Plane_PRD_Draft.md §5's "aligned to the actual
# org-baseline policy in use" — the exact policy the 100 mock cases' scores
# were hand-authored against.
DEMO_ORG_POLICY = {
    "input_checks_enabled": {"secrets": True, "injection": True, "pii": True},
    "output_t0_checks_enabled": {"secrets": True, "canary": True},
    "output_t1_checks_enabled": {"pii": True, "toxicity": False},
    "input_pii_review_threshold": 0.4,
    "input_pii_redact_threshold": 0.8,
    "output_pii_review_threshold": 0.4,
    "output_pii_redact_threshold": 0.8,
    "toxicity_regenerate_threshold": 0.5,
    "toxicity_block_threshold": 0.85,
    "toxicity_mild_action": "flag_visible",
    "fail_mode": "open",
    "locks": ["output_pii_redact_threshold"],
}


async def ensure_demo_org(pool) -> None:
    if await get_demo_org(pool) is not None:
        return

    async with pool.acquire() as conn, conn.transaction():
        # Password is random and never surfaced anywhere — nobody logs into
        # this org via POST /auth/login; POST /auth/demo-login hands out a
        # token for it with no credential at all (see routers/auth.py).
        org = await conn.fetchrow(
            """
            INSERT INTO organizations (name, email, password_hash, is_demo)
            VALUES ($1, $2, $3, true)
            RETURNING id, name
            """,
            DEMO_ORG_NAME,
            DEMO_ORG_EMAIL,
            hash_password(uuid.uuid4().hex),
        )

        await insert_org_layer(conn, org["id"], DEMO_ORG_POLICY)
        agent = await create_agent(conn, org["id"], DEMO_AGENT_NAME)
        await insert_agent_layer(conn, org["id"], agent["id"], {})

        bundle, clamp_events = compile_bundle(
            DEMO_ORG_POLICY, {}, policy_name=DEMO_AGENT_NAME, version=1,
            source_layers=[DEMO_ORG_NAME, DEMO_AGENT_NAME],
        )
        await insert_bundle(
            conn,
            organization_id=org["id"],
            agent_id=agent["id"],
            version=bundle["_meta"]["version"],
            hash_=bundle["_meta"]["hash"],
            source_layers=bundle["_meta"]["source_layers"],
            fields=bundle["fields"],
            clamp_events=clamp_events,
        )
