from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_plane.detectors import pii, toxicity
from db.connection import close_pool, get_pool
from routers import agents, auth, check, ledger, policies


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    # PRD §4.4: models warmed at startup, not on the first request
    pii.warm_up()
    toxicity.warm_up()
    yield
    await close_pool()


app = FastAPI(title="ControlPlane.ai", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(policies.router, prefix="/policies", tags=["policies"])
app.include_router(check.router, tags=["check"])
app.include_router(ledger.router, prefix="/ledger", tags=["ledger"])
