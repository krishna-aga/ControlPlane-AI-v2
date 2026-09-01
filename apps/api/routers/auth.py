from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from auth import create_token, hash_password, verify_password
from db.connection import get_pool

router = APIRouter()


class SignupIn(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _org_out(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


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
        RETURNING id, name, email, created_at
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
        "SELECT id, name, email, password_hash FROM organizations WHERE email = $1", body.email
    )
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {"token": create_token(row["id"]), "organization": _org_out(row)}
