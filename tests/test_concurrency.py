"""The key correctness property: one driver is never given two orders at once,
even when many dispatch attempts race (e.g. several API replicas)."""
import asyncio

import fakeredis
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Driver, DriverStatus, Order, OrderStatus
from app.services import location
from app.services.dispatch import dispatch_order
from tests.conftest import reset_db


async def test_race_one_driver_many_orders():
    await reset_db()
    r = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)

    async with SessionLocal() as s:
        s.add(Driver(id=1, name="Solo", phone="+911", status=DriverStatus.AVAILABLE))
        for i in range(10):
            s.add(Order(customer_name=f"c{i}", pickup_lat=12.97, pickup_lng=77.59,
                        dropoff_lat=12.95, dropoff_lng=77.60, status=OrderStatus.PENDING))
        await s.commit()
    await location.set_status(r, 1, "available")
    await location.record_location(r, 1, 12.971, 77.591)

    async def attempt(order_id):
        async with SessionLocal() as s:
            return await dispatch_order(s, r, order_id)

    # 10 orders fight over 1 driver concurrently
    await asyncio.gather(*(attempt(i) for i in range(1, 11)))

    async with SessionLocal() as s:
        assigned = (await s.execute(select(Order).where(Order.status == OrderStatus.ASSIGNED))).scalars().all()
        driver = await s.get(Driver, 1)
    assert len(assigned) == 1
    assert driver.status == DriverStatus.BUSY
