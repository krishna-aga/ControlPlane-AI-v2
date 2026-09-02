import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_org
from db.connection import get_pool
from db.queries import (
    get_ledger_row_for_org,
    get_ledger_summary,
    list_all_ledger_for_verify,
    list_ledger,
    set_review_verdict,
)

router = APIRouter()

_RECORD_FIELDS = (
    "request_id", "stage", "bundle_id", "tier_reached", "action",
    "review_needed", "modifiers", "contributing_signals",
)


def _ledger_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


class ReviewIn(BaseModel):
    verdict: str  # "approved" | "rejected"


@router.get("")
async def get_ledger(
    agent_id: int | None = None,
    review_needed: bool | None = None,
    flagged: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    org=Depends(get_current_org),
):
    pool = await get_pool()
    rows = await list_ledger(
        pool, org["id"], agent_id=agent_id, review_needed=review_needed, flagged=flagged, limit=limit, offset=offset
    )
    return [dict(r) for r in rows]


@router.get("/summary")
async def ledger_summary(agent_id: int | None = None, org=Depends(get_current_org)):
    """KPIs for the Audit Ledger page (PRD §0.1): total logs, action
    breakdown, stage breakdown, most recent entry timestamp."""
    pool = await get_pool()
    return await get_ledger_summary(pool, org["id"], agent_id=agent_id)


@router.get("/verify")
async def verify_ledger(org=Depends(get_current_org)):
    """Walks the entire hash chain (not scoped to this org — the chain is
    global, per context.md), recomputing each row's hash from its own
    contents + the previous row's hash, and confirms it matches what's
    stored. Returns the first broken row, if any — this is the endpoint
    Open Question #5 was tracking."""
    pool = await get_pool()
    rows = await list_all_ledger_for_verify(pool)

    prev_hash = None
    for i, row in enumerate(rows):
        # asyncpg decodes the `request_id` UUID column back into a
        # uuid.UUID object, but the hash was originally computed over the
        # str() form (routers/check.py generates request_id as str(uuid4())
        # before the row is ever inserted) — str() it back here or every
        # row's recomputed hash mismatches the stored one.
        record = {
            "prev_hash": prev_hash,
            **{k: row[k] for k in _RECORD_FIELDS if k != "request_id"},
            "request_id": str(row["request_id"]),
        }
        expected = _ledger_hash(record)
        if row["prev_hash"] != prev_hash or row["hash"] != expected:
            return {
                "ok": False,
                "broken_at_id": row["id"],
                "request_id": str(row["request_id"]),
                "expected_hash": expected,
                "stored_hash": row["hash"],
                "expected_prev_hash": prev_hash,
                "stored_prev_hash": row["prev_hash"],
                "rows_checked": i + 1,
            }
        prev_hash = row["hash"]

    return {"ok": True, "rows_checked": len(rows)}


@router.post("/{ledger_id}/review")
async def review_ledger_row(ledger_id: int, body: ReviewIn, org=Depends(get_current_org)):
    if body.verdict not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="verdict must be 'approved' or 'rejected'")

    pool = await get_pool()
    row = await get_ledger_row_for_org(pool, ledger_id, org["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="ledger row not found")

    updated = await set_review_verdict(pool, ledger_id, body.verdict, org["email"])
    return dict(updated)
