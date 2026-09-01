from typing import Union

import asyncpg

# Every function here takes either a Pool or a Connection (e.g. one held inside
# a transaction) — both expose the same fetchrow/fetchval/execute interface.
Conn = Union[asyncpg.Pool, asyncpg.Connection]


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
) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        INSERT INTO bundles (organization_id, agent_id, version, hash, source_layers, fields, clamp_events)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, organization_id, agent_id, version, hash, source_layers, fields, clamp_events, compiled_at
        """,
        organization_id,
        agent_id,
        version,
        hash_,
        source_layers,
        fields,
        clamp_events,
    )


async def get_latest_bundle(pool: Conn, agent_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT id, organization_id, agent_id, version, hash, source_layers, fields, clamp_events, compiled_at
        FROM bundles
        WHERE agent_id = $1
        ORDER BY version DESC
        LIMIT 1
        """,
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
