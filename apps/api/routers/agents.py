from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_org
from db.connection import get_pool
from db.queries import (
    create_agent,
    get_agent,
    get_latest_agent_layer,
    get_latest_bundle,
    insert_agent_layer,
    list_agents,
    list_bundles,
    promote_bundle,
)
from routers._compile_helper import compile_and_persist_for_agent

router = APIRouter()


class AgentIn(BaseModel):
    name: str


class LayerIn(BaseModel):
    content: dict
    promote: bool = True
    label: str | None = None


async def _get_owned_agent(pool, org_id: int, agent_id: int) -> dict:
    agent = await get_agent(pool, agent_id)
    if agent is None or agent["organization_id"] != org_id:
        raise HTTPException(status_code=404, detail="agent not found")
    return dict(agent)


@router.get("")
async def get_agents(org=Depends(get_current_org)):
    pool = await get_pool()
    rows = await list_agents(pool, org["id"])
    return [dict(r) for r in rows]


@router.post("")
async def create_new_agent(body: AgentIn, org=Depends(get_current_org)):
    pool = await get_pool()
    existing = await pool.fetchrow(
        "SELECT id FROM agents WHERE organization_id = $1 AND name = $2", org["id"], body.name
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="an agent with this name already exists")
    row = await create_agent(pool, org["id"], body.name)
    return dict(row)


@router.get("/{agent_id}")
async def get_agent_detail(agent_id: int, org=Depends(get_current_org)):
    pool = await get_pool()
    agent = await _get_owned_agent(pool, org["id"], agent_id)
    layer = await get_latest_agent_layer(pool, agent_id)
    bundle = await get_latest_bundle(pool, agent_id)
    return {"agent": agent, "layer": dict(layer) if layer else None, "bundle": dict(bundle) if bundle else None}


@router.get("/{agent_id}/policy")
async def get_agent_policy(agent_id: int, org=Depends(get_current_org)):
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)
    layer = await get_latest_agent_layer(pool, agent_id)
    if layer is None:
        raise HTTPException(status_code=404, detail="no policy set for this agent yet")
    return dict(layer)


@router.post("/{agent_id}/policy")
async def post_agent_policy(agent_id: int, body: LayerIn, org=Depends(get_current_org)):
    pool = await get_pool()
    agent = await _get_owned_agent(pool, org["id"], agent_id)

    async with pool.acquire() as conn, conn.transaction():
        layer_row = await insert_agent_layer(conn, org["id"], agent_id, body.content)
        bundle, clamp_events = await compile_and_persist_for_agent(
            conn, org["id"], agent_id, org["name"], agent["name"], promote=body.promote, label=body.label
        )
        if bundle is None:
            raise HTTPException(status_code=422, detail="set an org policy before compiling an agent")

    return {"layer": dict(layer_row), "bundle": bundle, "clamp_events": clamp_events}


@router.get("/{agent_id}/bundles")
async def get_agent_bundles(agent_id: int, org=Depends(get_current_org)):
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)
    rows = await list_bundles(pool, agent_id)
    return [dict(r) for r in rows]


@router.post("/{agent_id}/bundles/{bundle_id}/promote")
async def promote_agent_bundle(agent_id: int, bundle_id: int, org=Depends(get_current_org)):
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)
    row = await promote_bundle(pool, agent_id, bundle_id)
    if row is None:
        raise HTTPException(status_code=404, detail="bundle not found for this agent")
    return dict(row)
