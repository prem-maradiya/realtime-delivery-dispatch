"""Authentication: password hashing, JWT tokens and role-based access.

Roles:
  admin     operations staff: onboards drivers, sees everything, can cancel any order
  driver    can only update their own status/location and their own assigned orders
  customer  can only create, view, track and cancel their own orders
"""
import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

Role = Literal["admin", "driver", "customer"]


# ---------- passwords (PBKDF2-SHA256, stdlib only) ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    n = settings.password_hash_iterations
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
    # Store algorithm, cost and salt with the hash so the cost can be raised later.
    return f"pbkdf2_sha256${n}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(n))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))  # constant-time compare
    except (ValueError, TypeError):
        return False


# ---------- tokens ----------
@dataclass(frozen=True)
class Principal:
    """Who is making the request."""
    role: Role
    id: int | None  # driver/customer id; None for admin

    def is_driver(self, driver_id: int | None) -> bool:
        return self.role == "driver" and driver_id is not None and self.id == driver_id

    def is_customer(self, customer_id: int | None) -> bool:
        return self.role == "customer" and customer_id is not None and self.id == customer_id


def create_token(role: Role, subject_id: int | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "admin" if subject_id is None else str(subject_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Principal:
    """Raises ValueError if the token is invalid or expired."""
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        role = data["role"]
        if role not in ("admin", "driver", "customer"):
            raise ValueError("bad role")
        return Principal(role=role, id=None if role == "admin" else int(data["sub"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as e:
        raise ValueError("invalid or expired token") from e


# ---------- FastAPI dependencies ----------
_bearer = HTTPBearer(auto_error=False)


async def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return decode_token(creds.credentials)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})


def require_role(*roles: Role):
    """Dependency factory: Depends(require_role("admin", "driver"))."""
    async def checker(p: Principal = Depends(get_principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed for your role")
        return p
    return checker


def forbidden(detail: str = "not allowed") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)
