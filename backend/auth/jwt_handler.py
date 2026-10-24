"""
JWT session token handling. Per the spec, a basic JWT carrying user_id
and role is sufficient for this prototype - no full SSO/OAuth.

SECRET_KEY is generated at process start rather than hardcoded. This
means tokens don't survive a server restart (acceptable for a local
demo/hackathon prototype); a production deployment would load this
from a secret manager / environment variable instead.
"""
import os
import secrets
from datetime import datetime, timedelta
from jose import jwt, JWTError

SECRET_KEY = os.environ.get("QUALITYGUARD_JWT_SECRET", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60  # 12 hours - convenient for a demo session


def create_access_token(user_id: int, username: str, role: str, line_scope: str | None) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "line_scope": line_scope,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
