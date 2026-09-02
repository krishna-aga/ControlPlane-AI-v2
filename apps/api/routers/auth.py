from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from auth import create_token, hash_password, verify_password
from db.connection import get_pool
from db.queries import get_demo_org

router = APIRouter()


class SignupIn(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _org_out(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"], "is_demo": row["is_demo"]}


@router.post("/signup")
async def signup(body: SignupIn):
    pool = await get_pool()
    existing = await pool.fetchrow("SELECT id FROM organizations WHERE email = $1", body.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="an organization with this email already exists")

    row = await pool.fetchrow(
        """
        INSERT INTO organizations (name, email, password_hash)
        VALUES ($1, $2, $3)
        RETURNING id, name, email, is_demo, created_at
        """,
        body.name,
        body.email,
        hash_password(body.password),
    )
    return {"token": create_token(row["id"]), "organization": _org_out(row)}


@router.post("/login")
async def login(body: LoginIn):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, email, is_demo, password_hash FROM organizations WHERE email = $1", body.email
    )
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {"token": create_token(row["id"]), "organization": _org_out(row)}


@router.post("/demo-login")
async def demo_login():
    """No credentials needed — hands out a fresh token for the single
    auto-seeded Demo Org (learning_plane/demo_seed.py). That org exists
    purely to host the mock 100-case calibration dataset in a context where
    it's actually valid (its policy matches exactly what the mock data was
    authored against); there's nothing behind it a real org's login should
    protect."""
    pool = await get_pool()
    row = await get_demo_org(pool)
    if row is None:
        raise HTTPException(status_code=503, detail="demo org not seeded yet — try again shortly")
    return {"token": create_token(row["id"]), "organization": _org_out(row)}
