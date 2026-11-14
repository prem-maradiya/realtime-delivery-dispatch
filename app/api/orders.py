from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Order, OrderEvent, OrderStatus
from app.redis_client import get_redis
from app.schemas import CancelRequest, OrderCreate, OrderEventOut, OrderOut
from app.services import orders as order_service
from app.services.errors import Conflict, NotFound

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(data: OrderCreate, session: AsyncSession = Depends(get_session),
                       r: Redis = Depends(get_redis)):
    """Creates the order and immediately tries to dispatch it to the nearest driver."""
    return await order_service.create_order(session, r, data)


@router.get("", response_model=list[OrderOut])
async def list_orders(
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, le=500),
    session: AsyncSession = Depends(get_session),
):
    q = select(Order).order_by(Order.id.desc()).limit(limit)
    if status_filter:
        q = q.where(Order.status == status_filter)
    return (await session.execute(q)).scalars().all()


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, session: AsyncSession = Depends(get_session)):
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return order


@router.get("/{order_id}/events", response_model=list[OrderEventOut])
async def order_events(order_id: int, session: AsyncSession = Depends(get_session)):
    """Full audit trail of the order's status history."""
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
async def pickup(order_id: int, driver_id: int = Query(...),
                 session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis)):
    return await _transition(session, r, order_id, OrderStatus.PICKED_UP, driver_id, "picked up")


@router.post("/{order_id}/deliver", response_model=OrderOut)
async def deliver(order_id: int, driver_id: int = Query(...),
                  session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis)):
    return await _transition(session, r, order_id, OrderStatus.DELIVERED, driver_id, "delivered")


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel(order_id: int, body: CancelRequest | None = None,
                 session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis)):
    note = (body.reason if body else None) or "cancelled"
    return await _transition(session, r, order_id, OrderStatus.CANCELLED, note=note)
