from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_org
from control_plane.resolve import resolve
from db.connection import get_pool
from db.queries import (
    get_agent,
    get_latest_agent_layer,
    get_latest_org_layer,
    get_production_bundle,
    list_ledger,
    list_output_rows_for_agent,
)
from learning_plane.mock_eval import run_mock_eval
from learning_plane.metrics import compute_full_calibration, compute_metrics
from learning_plane.replay import replay_output_decision, split_signals

router = APIRouter()


async def _get_owned_agent(pool, org_id: int, agent_id: int) -> dict:
    agent = await get_agent(pool, agent_id)
    if agent is None or agent["organization_id"] != org_id:
        raise HTTPException(status_code=404, detail="agent not found")
    return dict(agent)


async def _get_production_fields(pool, agent_id: int) -> dict:
    bundle_row = await get_production_bundle(pool, agent_id)
    if bundle_row is None:
        raise HTTPException(status_code=422, detail="agent has no production bundle yet")
    return bundle_row["fields"]


def _require_demo_org(org: dict) -> None:
    """The mock 100-case dataset is authored against exactly one policy
    (learning_plane/demo_seed.py's DEMO_ORG_POLICY) — it isn't meaningful
    against a real org's own agents, which may run completely different
    thresholds. Restricted to the auto-seeded Demo Org so it can't be shown,
    even via a direct API call, somewhere it would misrepresent real data."""
    if not org["is_demo"]:
        raise HTTPException(
            status_code=403,
            detail="the mock calibration dataset only applies to the Demo Org — see .agents/Learning_Plane_PRD_Draft.md §5",
        )


# ---- Mock eval / calibration + metrics (PRD §3/§4/§5/§6) ----
# No detector call, no LLM call, no ledger write, ever — recomputed fresh
# from the static 100-case mock dataset against whichever bundle is
# currently production for this agent. Demo Org only — see _require_demo_org.

@router.get("/agents/{agent_id}/mock-eval")
async def get_mock_eval(agent_id: int, org=Depends(get_current_org)):
    _require_demo_org(org)
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)
    fields = await _get_production_fields(pool, agent_id)
    return {"cases": run_mock_eval(fields)}


@router.get("/agents/{agent_id}/mock-eval/metrics")
async def get_mock_eval_metrics(agent_id: int, org=Depends(get_current_org)):
    _require_demo_org(org)
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)
    fields = await _get_production_fields(pool, agent_id)
    return compute_metrics(run_mock_eval(fields))


@router.get("/agents/{agent_id}/mock-eval/calibration")
async def get_mock_eval_calibration(agent_id: int, org=Depends(get_current_org)):
    _require_demo_org(org)
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)
    await _get_production_fields(pool, agent_id)  # gate: agent must have a production bundle
    return compute_full_calibration()


# ---- Shadow deploy (PRD §1.1) ----

class ShadowDeployIn(BaseModel):
    version_a: int
    version_b: int


@router.post("/agents/{agent_id}/shadow-deploy")
async def shadow_deploy(agent_id: int, body: ShadowDeployIn, org=Depends(get_current_org)):
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)

    bundle_rows = await pool.fetch(
        "SELECT * FROM bundles WHERE agent_id = $1 AND version = ANY($2::int[])", agent_id, [body.version_a, body.version_b]
    )
    by_version = {r["version"]: r for r in bundle_rows}
    if body.version_a not in by_version or body.version_b not in by_version:
        raise HTTPException(status_code=404, detail="one or both versions not found for this agent")

    fields_a = by_version[body.version_a]["fields"]
    fields_b = by_version[body.version_b]["fields"]

    rows = await list_output_rows_for_agent(pool, agent_id)
    table = []
    changed = 0
    for row in rows:
        t0, t1 = split_signals(row["contributing_signals"])
        decision_a = replay_output_decision(t0, t1, fields_a)
        decision_b = replay_output_decision(t0, t1, fields_b)
        differs = decision_a != decision_b
        if differs:
            changed += 1
        table.append({
            "request_id": str(row["request_id"]),
            "version_a": decision_a,
            "version_b": decision_b,
            "differs": differs,
        })

    return {
        "version_a": body.version_a,
        "version_b": body.version_b,
        "total_rows": len(table),
        "changed_count": changed,
        "rows": table,
    }


# ---- Calibration -> new policy version, clamp preview (PRD §6.1 step 3) ----

class CalibrationPreviewIn(BaseModel):
    field: str
    value: float


@router.post("/agents/{agent_id}/calibration-preview")
async def calibration_preview(agent_id: int, body: CalibrationPreviewIn, org=Depends(get_current_org)):
    _require_demo_org(org)
    pool = await get_pool()
    await _get_owned_agent(pool, org["id"], agent_id)

    org_row = await get_latest_org_layer(pool, org["id"])
    agent_row = await get_latest_agent_layer(pool, agent_id)
    if org_row is None or agent_row is None:
        raise HTTPException(status_code=422, detail="set an org and agent policy before previewing calibration")

    candidate_agent_content = deepcopy(agent_row["content"])
    candidate_agent_content[body.field] = body.value

    resolved_fields, clamp_events = resolve(org_row["content"], candidate_agent_content, org_name=org["name"])
    relevant_clamp = next((e for e in clamp_events if e["field"] == body.field), None)

    return {
        "requested_value": body.value,
        "effective_value": resolved_fields.get(body.field),
        "would_be_clamped": relevant_clamp is not None,
        "clamp_event": relevant_clamp,
    }


# ---- Reviewer queue (PRD §2) ----

@router.get("/reviewer-queue")
async def reviewer_queue(agent_id: int | None = None, limit: int = 100, offset: int = 0, org=Depends(get_current_org)):
    pool = await get_pool()
    input_rows = await list_ledger(pool, org["id"], agent_id=agent_id, review_needed=True, limit=limit, offset=offset)
    output_rows = await list_ledger(pool, org["id"], agent_id=agent_id, flagged=True, limit=limit, offset=offset)
    return {"input_review_needed": [dict(r) for r in input_rows], "output_flagged": [dict(r) for r in output_rows]}
