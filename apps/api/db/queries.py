from typing import Union

import asyncpg

# Every function here takes either a Pool or a Connection (e.g. one held inside
# a transaction) — both expose the same fetchrow/fetchval/execute interface.
Conn = Union[asyncpg.Pool, asyncpg.Connection]


# ---- Organizations ----

async def get_demo_org(pool: Conn) -> asyncpg.Record | None:
    """The single auto-seeded org the mock calibration dataset is valid
    against (learning_plane/demo_seed.py) — at most one row can ever have
    is_demo=true (enforced by a partial unique index, migration 0005)."""
    return await pool.fetchrow("SELECT id, name, email, is_demo FROM organizations WHERE is_demo LIMIT 1")


# ---- Agents ----

async def create_agent(pool: Conn, organization_id: int, name: str) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        INSERT INTO agents (organization_id, name)
        VALUES ($1, $2)
        RETURNING id, organization_id, name, created_at
        """,
        organization_id,
        name,
    )


async def list_agents(pool: Conn, organization_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, organization_id, name, created_at FROM agents WHERE organization_id = $1 ORDER BY id",
        organization_id,
    )


async def get_agent(pool: Conn, agent_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT id, organization_id, name, created_at FROM agents WHERE id = $1", agent_id
    )


# ---- Policy layers ----

async def get_latest_org_layer(pool: Conn, organization_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT id, layer_type, organization_id, agent_id, version, content, created_at
        FROM policy_layers
        WHERE layer_type = 'org' AND organization_id = $1
        ORDER BY version DESC
        LIMIT 1
        """,
        organization_id,
    )


async def insert_org_layer(pool: Conn, organization_id: int, content: dict) -> asyncpg.Record:
    latest = await get_latest_org_layer(pool, organization_id)
    next_version = (latest["version"] + 1) if latest else 1
    return await pool.fetchrow(
        """
        INSERT INTO policy_layers (layer_type, organization_id, agent_id, version, content)
        VALUES ('org', $1, NULL, $2, $3)
        RETURNING id, layer_type, organization_id, agent_id, version, content, created_at
        """,
        organization_id,
        next_version,
        content,
    )


async def get_latest_agent_layer(pool: Conn, agent_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT id, layer_type, organization_id, agent_id, version, content, created_at
        FROM policy_layers
        WHERE layer_type = 'agent' AND agent_id = $1
        ORDER BY version DESC
        LIMIT 1
        """,
        agent_id,
    )


async def insert_agent_layer(
    pool: Conn, organization_id: int, agent_id: int, content: dict
) -> asyncpg.Record:
    latest = await get_latest_agent_layer(pool, agent_id)
    next_version = (latest["version"] + 1) if latest else 1
    return await pool.fetchrow(
        """
        INSERT INTO policy_layers (layer_type, organization_id, agent_id, version, content)
        VALUES ('agent', $1, $2, $3, $4)
        RETURNING id, layer_type, organization_id, agent_id, version, content, created_at
        """,
        organization_id,
        agent_id,
        next_version,
        content,
    )


# ---- Bundles ----

BUNDLE_COLUMNS = "id, organization_id, agent_id, version, hash, source_layers, fields, clamp_events, is_production, label, compiled_at"


async def get_latest_bundle_version(pool: Conn, agent_id: int) -> int:
    version = await pool.fetchval("SELECT MAX(version) FROM bundles WHERE agent_id = $1", agent_id)
    return version or 0


async def insert_bundle(
    pool: Conn,
    organization_id: int,
    agent_id: int,
    version: int,
    hash_: str,
    source_layers: list[str],
    fields: dict,
    clamp_events: list[dict],
    promote: bool = True,
    label: str | None = None,
) -> asyncpg.Record:
    """`promote=True` (the default, used by the existing policy-edit
    endpoints) makes this version production immediately, unsetting whatever
    was production before — preserves the pre-learning-plane behavior where
    compiling a policy is what the chat demo/`/check` actually enforces.
    `promote=False` is used only by the calibration flow (PRD §6.1 step 5):
    the new version is stored but sits alongside the existing production
    bundle until someone explicitly promotes it."""
    if promote:
        await pool.execute("UPDATE bundles SET is_production = false WHERE agent_id = $1", agent_id)
    return await pool.fetchrow(
        f"""
        INSERT INTO bundles (organization_id, agent_id, version, hash, source_layers, fields, clamp_events, is_production, label)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING {BUNDLE_COLUMNS}
        """,
        organization_id,
        agent_id,
        version,
        hash_,
        source_layers,
        fields,
        clamp_events,
        promote,
        label,
    )


async def get_latest_bundle(pool: Conn, agent_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        f"""
        SELECT {BUNDLE_COLUMNS}
        FROM bundles
        WHERE agent_id = $1
        ORDER BY version DESC
        LIMIT 1
        """,
        agent_id,
    )


async def get_production_bundle(pool: Conn, agent_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        f"SELECT {BUNDLE_COLUMNS} FROM bundles WHERE agent_id = $1 AND is_production LIMIT 1",
        agent_id,
    )


async def list_bundles(pool: Conn, agent_id: int) -> list[asyncpg.Record]:
    return await pool.fetch(
        f"SELECT {BUNDLE_COLUMNS} FROM bundles WHERE agent_id = $1 ORDER BY version DESC",
        agent_id,
    )


async def get_bundle(pool: Conn, bundle_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(f"SELECT {BUNDLE_COLUMNS} FROM bundles WHERE id = $1", bundle_id)


async def promote_bundle(pool: Conn, agent_id: int, bundle_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE bundles SET is_production = false WHERE agent_id = $1", agent_id)
        return await conn.fetchrow(
            f"""
            UPDATE bundles SET is_production = true
            WHERE id = $1 AND agent_id = $2
            RETURNING {BUNDLE_COLUMNS}
            """,
            bundle_id,
            agent_id,
        )


# ---- Ledger ----

async def get_last_ledger_hash(pool: Conn) -> str | None:
    return await pool.fetchval("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1")


async def insert_ledger_row(
    pool: Conn,
    *,
    request_id: str,
    stage: str,
    bundle_id: int,
    tier_reached: str | None,
    action: str,
    review_needed: bool | None,
    modifiers: list[str] | None,
    contributing_signals: list[dict],
    prev_hash: str | None,
    hash_: str,
) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        INSERT INTO ledger (
            request_id, stage, bundle_id, tier_reached, action,
            review_needed, modifiers, contributing_signals, prev_hash, hash
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING *
        """,
        request_id,
        stage,
        bundle_id,
        tier_reached,
        action,
        review_needed,
        modifiers,
        contributing_signals,
        prev_hash,
        hash_,
    )


async def get_ledger_row_for_org(pool: Conn, ledger_id: int, organization_id: int) -> asyncpg.Record | None:
    """Ownership-checked read: a ledger row only belongs to an org via its
    bundle's agent, since the ledger table itself carries no org/agent
    column directly (PRD: ledger rows are bundle-scoped, not tenant-scoped
    at the row level)."""
    return await pool.fetchrow(
        """
        SELECT l.* FROM ledger l
        JOIN bundles b ON b.id = l.bundle_id
        WHERE l.id = $1 AND b.organization_id = $2
        """,
        ledger_id,
        organization_id,
    )


async def list_ledger(
    pool: Conn,
    organization_id: int,
    agent_id: int | None = None,
    review_needed: bool | None = None,
    flagged: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[asyncpg.Record]:
    conditions = ["b.organization_id = $1"]
    params: list = [organization_id]

    if agent_id is not None:
        params.append(agent_id)
        conditions.append(f"b.agent_id = ${len(params)}")
    if review_needed is not None:
        params.append(review_needed)
        conditions.append(f"l.review_needed = ${len(params)}")
    if flagged is not None:
        clause = "'flag' = ANY (SELECT jsonb_array_elements_text(coalesce(l.modifiers, '[]'::jsonb)))"
        conditions.append(clause if flagged else f"NOT ({clause})")

    params.append(limit)
    limit_idx = len(params)
    params.append(offset)
    offset_idx = len(params)

    rows = await pool.fetch(
        f"""
        SELECT l.*, b.agent_id
        FROM ledger l
        JOIN bundles b ON b.id = l.bundle_id
        WHERE {' AND '.join(conditions)}
        ORDER BY l.id DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
        """,
        *params,
    )
    return rows


async def list_all_ledger_for_verify(pool: Conn) -> list[asyncpg.Record]:
    """Every row, oldest first — the order the hash chain was actually built
    in, needed to recompute and check it end to end."""
    return await pool.fetch("SELECT * FROM ledger ORDER BY id ASC")


async def set_review_verdict(pool: Conn, ledger_id: int, verdict: str, reviewed_by: str) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        UPDATE ledger SET review_verdict = $2, reviewed_by = $3, reviewed_at = now()
        WHERE id = $1
        RETURNING *
        """,
        ledger_id,
        verdict,
        reviewed_by,
    )


async def list_output_rows_for_agent(pool: Conn, agent_id: int) -> list[asyncpg.Record]:
    """Every output-stage ledger row ever written under any bundle belonging
    to this agent — the full replay population for shadow deploy (PRD §1.1:
    "no pre-filtering")."""
    return await pool.fetch(
        """
        SELECT l.* FROM ledger l
        JOIN bundles b ON b.id = l.bundle_id
        WHERE b.agent_id = $1 AND l.stage = 'output'
        ORDER BY l.id ASC
        """,
        agent_id,
    )


async def get_ledger_summary(pool: Conn, organization_id: int, agent_id: int | None = None) -> dict:
    """KPIs for the Audit Ledger page (PRD §0.1): total row count, a
    breakdown by action, a breakdown by stage, and the most recent entry's
    timestamp. Real ledger data only — never touches the mock eval set."""
    conditions = ["b.organization_id = $1"]
    params: list = [organization_id]
    if agent_id is not None:
        params.append(agent_id)
        conditions.append(f"b.agent_id = ${len(params)}")
    where = " AND ".join(conditions)

    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM ledger l JOIN bundles b ON b.id = l.bundle_id WHERE {where}", *params
    )
    by_action = await pool.fetch(
        f"""
        SELECT l.action, COUNT(*) AS count FROM ledger l
        JOIN bundles b ON b.id = l.bundle_id WHERE {where}
        GROUP BY l.action
        """,
        *params,
    )
    by_stage = await pool.fetch(
        f"""
        SELECT l.stage, COUNT(*) AS count FROM ledger l
        JOIN bundles b ON b.id = l.bundle_id WHERE {where}
        GROUP BY l.stage
        """,
        *params,
    )
    last_entry_at = await pool.fetchval(
        f"SELECT MAX(l.created_at) FROM ledger l JOIN bundles b ON b.id = l.bundle_id WHERE {where}", *params
    )

    return {
        "total": total,
        "by_action": {r["action"]: r["count"] for r in by_action},
        "by_stage": {r["stage"]: r["count"] for r in by_stage},
        "last_entry_at": last_entry_at.isoformat() if last_entry_at else None,
    }
