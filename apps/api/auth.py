import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Header, HTTPException

from db.connection import get_pool, with_db_retry

JWT_ALGORITHM = "HS256"
JWT_EXPIRES_HOURS = 24 * 7


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(organization_id: int) -> str:
    payload = {
        "organization_id": organization_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRES_HOURS),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


async def get_current_org(authorization: str = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    pool = await get_pool()
    org = await with_db_retry(
        pool.fetchrow,
        "SELECT id, name, email, is_demo, created_at FROM organizations WHERE id = $1",
        payload["organization_id"],
    )
    if org is None:
        raise HTTPException(status_code=401, detail="organization no longer exists")
    return dict(org)
