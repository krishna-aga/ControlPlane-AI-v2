from fastapi import APIRouter

router = APIRouter()

# TODO: POST /check — the orchestrator. Imports data_plane detectors + fusion,
# owns all branching (PRD §4.2 handle_request), writes the ledger row(s).
