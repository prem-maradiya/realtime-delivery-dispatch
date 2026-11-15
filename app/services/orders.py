"""Order lifecycle: create -> (dispatch) -> pickup -> deliver, or cancel."""
import asyncio

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ALLOWED_TRANSITIONS, Customer, Driver, DriverStatus, Order, OrderEvent, OrderStatus, utcnow
from app.schemas import OrderCreate, OrderOut
from app.services import events, location
from app.services.dispatch import dispatch_order, dispatch_pending
from app.services.errors import Conflict, NotFound


async def create_order(session: AsyncSession, r: Redis, data: OrderCreate, customer: Customer) -> Order:
    order = Order(
        customer_id=customer.id,
        customer_name=customer.name,
        pickup_lat=data.pickup.lat, pickup_lng=data.pickup.lng,
        dropoff_lat=data.dropoff.lat, dropoff_lng=data.dropoff.lng,
        status=OrderStatus.PENDING,
    )
    session.add(order)
    await session.flush()  # get order.id
    session.add(OrderEvent(order_id=order.id, status=OrderStatus.PENDING, note="order created"))
    await session.commit()

    await events.publish(r, events.DISPATCH_CHANNEL,
                         {"type": "order.created", "order": OrderOut.model_validate(order).model_dump(mode="json")})
    # Try to assign right away; if nobody is free, the sweeper retries later.
    return await dispatch_order(session, r, order.id)


async def transition(
    session: AsyncSession, r: Redis, order_id: int, new_status: OrderStatus,
    driver_id: int | None = None, note: str | None = None,
) -> Order:
    # SELECT ... FOR UPDATE: row lock so two requests can't change the same order at once.
    order = (await session.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    )).scalar_one_or_none()
    if order is None:
        raise NotFound(f"order {order_id} not found")
    # Build error messages BEFORE rollback: rollback expires the loaded object.
    error = None
    if new_status not in ALLOWED_TRANSITIONS[order.status]:
        error = f"cannot go from {order.status.value} to {new_status.value}"
    elif driver_id is not None and order.driver_id != driver_id:
        error = "order is not assigned to this driver"
    if error:
        await session.rollback()  # release the row lock
        raise Conflict(error)

    now = utcnow()
    order.status = new_status
    if new_status == OrderStatus.PICKED_UP:
        order.picked_up_at = now
    elif new_status == OrderStatus.DELIVERED:
        order.delivered_at = now

    freed_driver = None
    if new_status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED) and order.driver_id:
        driver = await session.get(Driver, order.driver_id, with_for_update=True)
        if driver is not None and driver.status == DriverStatus.BUSY:
            driver.status = DriverStatus.AVAILABLE
            freed_driver = driver.id

    session.add(OrderEvent(order_id=order.id, status=new_status, driver_id=order.driver_id, note=note))
    await session.commit()

    payload = {"type": "order.status_changed", "order": OrderOut.model_validate(order).model_dump(mode="json")}
    channels = [events.order_channel(order.id), events.DISPATCH_CHANNEL]
    if order.driver_id:
        channels.append(events.driver_channel(order.driver_id))
    await asyncio.gather(*(events.publish(r, c, payload) for c in channels))

    if freed_driver is not None:
        # Driver goes back into the GEO pool and can take waiting orders.
        await location.set_status(r, freed_driver, "available")
        await events.publish(r, events.DISPATCH_CHANNEL,
                             {"type": "driver.status_changed", "driver_id": freed_driver, "status": "available"})
        await dispatch_pending(session, r, limit=10)
        await session.refresh(order)
    return order
