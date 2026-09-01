from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_org
from db.connection import get_pool
from db.queries import get_latest_org_layer, insert_org_layer, list_agents
from routers._compile_helper import compile_and_persist_for_agent

router = APIRouter()


class LayerIn(BaseModel):
    content: dict


@router.get("/org")
async def get_org_policy(org=Depends(get_current_org)):
    pool = await get_pool()
    row = await get_latest_org_layer(pool, org["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="no org policy set yet")
    return dict(row)


@router.post("/org")
async def post_org_policy(body: LayerIn, org=Depends(get_current_org)):
    """Persists a new org-layer version, then recompiles every agent under
    this org — an org-level change (a tightened lock, a new threshold) can
    affect every agent's resolved bundle, not just the one being edited."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        layer_row = await insert_org_layer(conn, org["id"], body.content)
        agents = await list_agents(conn, org["id"])
        recompiled = []
        for agent in agents:
            bundle, clamp_events = await compile_and_persist_for_agent(
                conn, org["id"], agent["id"], org["name"], agent["name"]
            )
            recompiled.append({"agent_id": agent["id"], "agent_name": agent["name"], "bundle": bundle, "clamp_events": clamp_events})

    return {"layer": dict(layer_row), "recompiled_agents": recompiled}
