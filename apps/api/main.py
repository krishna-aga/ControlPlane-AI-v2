from fastapi import FastAPI

from routers import check, ledger, policies

app = FastAPI(title="ControlPlane.ai")

app.include_router(policies.router, prefix="/policies", tags=["policies"])
app.include_router(check.router, tags=["check"])
app.include_router(ledger.router, prefix="/ledger", tags=["ledger"])
