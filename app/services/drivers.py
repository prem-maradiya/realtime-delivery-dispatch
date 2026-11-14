"""Driver business logic."""
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Driver, DriverStatus
from app.services import events, location
from app.services.dispatch import dispatch_pending
from app.services.errors import Conflict, NotFound


async def change_status(session: AsyncSession, r: Redis, driver_id: int, new_status: DriverStatus) -> Driver:
    driver = await session.get(Driver, driver_id)
    if driver is None:
        raise NotFound(f"driver {driver_id} not found")

    # Conditional update: a driver who is BUSY (on a delivery) can't go online/offline by hand.
    res = await session.execute(
        update(Driver)
        .where(Driver.id == driver_id, Driver.status != DriverStatus.BUSY)
        .values(status=new_status)
    )
    if res.rowcount != 1:
        await session.rollback()
        raise Conflict("driver is on an active delivery")
    await session.commit()
    await session.refresh(driver)

    await location.set_status(r, driver_id, new_status.value)
    await events.publish(r, events.DISPATCH_CHANNEL,
                         {"type": "driver.status_changed", "driver_id": driver_id, "status": new_status.value})

    if new_status == DriverStatus.AVAILABLE:
        # A new driver came online: immediately try to serve waiting orders.
        await dispatch_pending(session, r, limit=10)
        await session.refresh(driver)
    return driver


async def persist_location(session: AsyncSession, r: Redis, driver_id: int) -> None:
    """Copy the live Redis location into Postgres (done periodically, not per ping)."""
    loc = await location.get_location(r, driver_id)
    if loc is None:
        return
    await session.execute(update(Driver).where(Driver.id == driver_id).values(last_lat=loc[0], last_lng=loc[1]))
    await session.commit()
