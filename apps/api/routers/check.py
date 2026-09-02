import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_org
from data_plane.llm_client import LLMError
from data_plane.pipeline import run_pipeline
from db.connection import get_pool
from db.queries import get_agent, get_production_bundle

router = APIRouter()
logger = logging.getLogger(__name__)


class CheckIn(BaseModel):
    prompt: str
    agent_id: int


@router.post("/check")
async def check(body: CheckIn, org=Depends(get_current_org)):
    pool = await get_pool()
    agent = await get_agent(pool, body.agent_id)
    if agent is None or agent["organization_id"] != org["id"]:
        raise HTTPException(status_code=404, detail="agent not found")

    bundle_row = await get_production_bundle(pool, body.agent_id)
    if bundle_row is None:
        raise HTTPException(status_code=404, detail="no compiled bundle for this agent yet — set org and agent policy first")

    try:
        return await run_pipeline(pool, bundle_row, body.prompt)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
