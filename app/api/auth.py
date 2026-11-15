import hmac

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Customer, Driver
from app.schemas import CustomerRegister, LoginRequest, TokenOut
from app.security import create_token, hash_password_async, verify_password_async

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")


@router.post("/customers/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register_customer(data: CustomerRegister, session: AsyncSession = Depends(get_session)):
    customer = Customer(name=data.name, phone=data.phone, password_hash=await hash_password_async(data.password))
    session.add(customer)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "phone already registered")
    return TokenOut(access_token=create_token("customer", customer.id), role="customer", id=customer.id)


@router.post("/login", response_model=TokenOut)
async def login(data: LoginRequest, session: AsyncSession = Depends(get_session)):
    """Exchange credentials for a JWT. Send it as `Authorization: Bearer <token>`
    (or `?token=<token>` for WebSockets, since browsers can't set WS headers)."""
    if data.role == "admin":
        ok = hmac.compare_digest(data.username, settings.admin_username) and \
             hmac.compare_digest(data.password, settings.admin_password)
        if not ok:
            raise _INVALID
        return TokenOut(access_token=create_token("admin", None), role="admin", id=None)

    model = Driver if data.role == "driver" else Customer
    user = (await session.execute(select(model).where(model.phone == data.username))).scalar_one_or_none()
    # Same error for "no such user" and "wrong password" so attackers can't probe phone numbers.
    if user is None or not await verify_password_async(data.password, user.password_hash):
        raise _INVALID
    return TokenOut(access_token=create_token(data.role, user.id), role=data.role, id=user.id)
