from fastapi import APIRouter

router = APIRouter()

# TODO: GET / (list ledger rows), GET /verify (walk the hash chain, confirm
# each row's hash against prev_hash + contents).
