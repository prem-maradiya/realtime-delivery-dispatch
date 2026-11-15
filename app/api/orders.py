from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Customer, Order, OrderEvent, OrderStatus
from app.redis_client import get_redis
from app.schemas import CancelRequest, OrderCreate, OrderEventOut, OrderOut
from app.security import Principal, get_principal, require_role
from app.services import orders as order_service
from app.services.errors import Conflict, NotFound

router = APIRouter(prefix="/orders", tags=["orders"])


def can_view(p: Principal, order: Order) -> bool:
    """Admin sees all; a customer sees their own; a driver sees orders assigned to them."""
    return p.role == "admin" or p.is_customer(order.customer_id) or p.is_driver(order.driver_id)


async def _get_visible_order(session: AsyncSession, order_id: int, p: Principal) -> Order:
    order = await session.get(Order, order_id)
    # 404 (not 403) for other people's orders, so ids can't be probed.
    if order is None or not can_view(p, order):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return order


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(data: OrderCreate, session: AsyncSession = Depends(get_session),
                       r: Redis = Depends(get_redis), p: Principal = Depends(require_role("customer"))):
    """Creates the order and immediately tries to dispatch it to the nearest driver."""
    customer = await session.get(Customer, p.id)
    if customer is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account no longer exists")
    return await order_service.create_order(session, r, data, customer)


@router.get("", response_model=list[OrderOut])
async def list_orders(
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, le=500),
    session: AsyncSession = Depends(get_session),
    p: Principal = Depends(get_principal),
):
    q = select(Order).order_by(Order.id.desc()).limit(limit)
    if p.role == "customer":
        q = q.where(Order.customer_id == p.id)
    elif p.role == "driver":
        q = q.where(Order.driver_id == p.id)
    if status_filter:
        q = q.where(Order.status == status_filter)
    return (await session.execute(q)).scalars().all()


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, session: AsyncSession = Depends(get_session),
                    p: Principal = Depends(get_principal)):
    return await _get_visible_order(session, order_id, p)


@router.get("/{order_id}/events", response_model=list[OrderEventOut])
async def order_events(order_id: int, session: AsyncSession = Depends(get_session),
                       p: Principal = Depends(get_principal)):
    """Full audit trail of the order's status history."""
    await _get_visible_order(session, order_id, p)
    rows = (await session.execute(
        select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.id)
    )).scalars().all()
    return rows


async def _transition(session, r, order_id, new_status, driver_id=None, note=None):
    try:
        return await order_service.transition(session, r, order_id, new_status, driver_id, note)
    except NotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.post("/{order_id}/pickup", response_model=OrderOut)
async def pickup(order_id: int, session: AsyncSession = Depends(get_session),
                 r: Redis = Depends(get_redis), p: Principal = Depends(require_role("driver"))):
    # The driver's identity comes from the token, never from the request body.
    return await _transition(session, r, order_id, OrderStatus.PICKED_UP, p.id, "picked up")


@router.post("/{order_id}/deliver", response_model=OrderOut)
async def deliver(order_id: int, session: AsyncSession = Depends(get_session),
                  r: Redis = Depends(get_redis), p: Principal = Depends(require_role("driver"))):
    return await _transition(session, r, order_id, OrderStatus.DELIVERED, p.id, "delivered")


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel(order_id: int, body: CancelRequest | None = None,
                 session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis),
                 p: Principal = Depends(require_role("admin", "customer"))):
    await _get_visible_order(session, order_id, p)
    note = (body.reason if body else None) or f"cancelled by {p.role}"
    await session.rollback()  # end the read transaction; transition() takes a row lock
    return await _transition(session, r, order_id, OrderStatus.CANCELLED, note=note)
