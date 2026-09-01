from fastapi import HTTPException

from control_plane.compile import compile_bundle
from db.queries import (
    get_latest_agent_layer,
    get_latest_bundle_version,
    get_latest_org_layer,
    insert_bundle,
)


async def compile_and_persist_for_agent(
    conn, organization_id: int, agent_id: int, org_name: str, agent_name: str
) -> tuple[dict | None, list[dict]]:
    """Shared by both policy endpoints — an org-layer edit recompiles every
    agent under that org, an agent-layer edit recompiles just that one agent.
    Returns (None, []) if either layer doesn't exist yet (org policy must be
    set before an agent can be compiled)."""
    org_row = await get_latest_org_layer(conn, organization_id)
    agent_row = await get_latest_agent_layer(conn, agent_id)
    if org_row is None or agent_row is None:
        return None, []

    try:
        bundle, clamp_events = compile_bundle(
            org_row["content"],
            agent_row["content"],
            policy_name=agent_name,
            version=await get_latest_bundle_version(conn, agent_id) + 1,
            source_layers=[org_name, agent_name],
        )
    except ValueError as e:
        # covers both CompileError (threshold guards) and validate_locks()'s
        # plain ValueError (a locks: entry not set in its own layer)
        raise HTTPException(status_code=422, detail=str(e))

    row = await insert_bundle(
        conn,
        organization_id=organization_id,
        agent_id=agent_id,
        version=bundle["_meta"]["version"],
        hash_=bundle["_meta"]["hash"],
        source_layers=bundle["_meta"]["source_layers"],
        fields=bundle["fields"],
        clamp_events=clamp_events,
    )
    return dict(row), clamp_events
