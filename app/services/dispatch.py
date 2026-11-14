"""The dispatch engine: match a PENDING order to the nearest AVAILABLE driver.

Algorithm (per order):
  1. Search the Redis GEO index in widening rings (2 km, 5 km, 10 km, 25 km).
  2. Walk candidates closest-first. Skip drivers whose heartbeat expired (stale app).
  3. Take a short Redis lock on the driver so no other worker/replica grabs them at the same time.
  4. In ONE Postgres transaction, run two conditional UPDATEs:
        drivers SET status=busy      WHERE id=? AND status='available'
        orders  SET status=assigned  WHERE id=? AND status='pending'
     If either touched 0 rows, someone else won the race -> roll back, try the next driver.
  5. Commit, update Redis, publish real-time events.

The Redis lock avoids wasted work; the conditional UPDATE is the real correctness
guarantee (it still holds if Redis is wrong or a lock expires early).
"""
import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Driver, DriverStatus, Order, OrderEvent, OrderStatus, utcnow
from app.schemas import OrderOut
from app.services import events, location
from app.services.locks import acquire_lock, release_lock

log = logging.getLogger(__name__)


async def dispatch_order(session: AsyncSession, r: Redis, order_id: int) -> Order | None:
    order = await session.get(Order, order_id)
    if order is None or order.status != OrderStatus.PENDING:
        return order

    await session.execute(
        update(Order).where(Order.id == order_id).values(dispatch_attempts=Order.dispatch_attempts + 1)
    )
    await session.commit()

    tried: set[int] = set()
    for radius in settings.search_radii_km:
        candidates = await location.find_nearby(
            r, order.pickup_lat, order.pickup_lng, radius, settings.candidates_per_ring
        )
        for driver_id, distance_km in candidates:
            if driver_id in tried:
                continue  # already tried in a smaller ring
            tried.add(driver_id)

            if not await location.is_alive(r, driver_id):
                # Lazy cleanup: driver stopped pinging (app killed, no network).
                await location.remove_from_pool(r, driver_id)
                continue

            assigned = await _try_assign(session, r, order_id, driver_id, distance_km)
            if assigned is not None:
                return assigned
            # Order may have been cancelled/assigned by someone else meanwhile.
            current = await session.get(Order, order_id, populate_existing=True)
            if current is None or current.status != OrderStatus.PENDING:
                return current

    log.info("order %s: no driver available yet (will retry)", order_id)
    return await session.get(Order, order_id, populate_existing=True)


async def _try_assign(
    session: AsyncSession, r: Redis, order_id: int, driver_id: int, distance_km: float
) -> Order | None:
    lock_key = f"lock:driver:{driver_id}"
    token = await acquire_lock(r, lock_key, settings.driver_lock_ttl_s)
    if token is None:
        return None  # another worker is assigning this driver right now
    try:
        driver_res = await session.execute(
            update(Driver)
            .where(Driver.id == driver_id, Driver.status == DriverStatus.AVAILABLE)
            .values(status=DriverStatus.BUSY)
        )
        if driver_res.rowcount != 1:
            await session.rollback()
            await location.remove_from_pool(r, driver_id)  # Redis was stale
            return None

        order_res = await session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == OrderStatus.PENDING)
            .values(status=OrderStatus.ASSIGNED, driver_id=driver_id,
                    assigned_at=utcnow(), assignment_distance_km=round(distance_km, 3))
        )
        if order_res.rowcount != 1:
            await session.rollback()  # also undoes the driver UPDATE above
            return None

        session.add(OrderEvent(order_id=order_id, status=OrderStatus.ASSIGNED, driver_id=driver_id,
                               note=f"nearest driver, {distance_km:.2f} km away"))
        await session.commit()
    finally:
        await release_lock(r, lock_key, token)

    # --- committed: now update hot state + notify everyone ---
    await location.set_status(r, driver_id, "busy", active_order=order_id)
    order = await session.get(Order, order_id, populate_existing=True)
    payload = {"type": "order.assigned", "order": OrderOut.model_validate(order).model_dump(mode="json")}
    await asyncio.gather(
        events.publish(r, events.order_channel(order_id), payload),
        events.publish(r, events.driver_channel(driver_id), payload),
        events.publish(r, events.DISPATCH_CHANNEL, payload),
    )
    log.info("order %s -> driver %s (%.2f km)", order_id, driver_id, distance_km)
    return order


async def dispatch_pending(session: AsyncSession, r: Redis, limit: int = 50) -> int:
    """Retry the oldest PENDING orders (FIFO fairness). Returns how many got a driver."""
    ids = (await session.execute(
        select(Order.id).where(Order.status == OrderStatus.PENDING).order_by(Order.created_at).limit(limit)
    )).scalars().all()
    assigned = 0
    for oid in ids:
        order = await dispatch_order(session, r, oid)
        if order is not None and order.status == OrderStatus.ASSIGNED:
            assigned += 1
    return assigned


async def sweeper_loop(r: Redis) -> None:
    """Background job: every few seconds, retry PENDING orders.

    With several API replicas, a Redis lock makes sure only ONE replica sweeps per tick.
    """
    from app.db import SessionLocal

    interval = settings.sweeper_interval_s
    while True:
        await asyncio.sleep(interval)
        try:
            token = await acquire_lock(r, "lock:sweeper", max(1, int(interval)))
            if token is None:
                continue
            async with SessionLocal() as session:
                n = await dispatch_pending(session, r)
                if n:
                    log.info("sweeper assigned %d pending orders", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sweeper failed")
