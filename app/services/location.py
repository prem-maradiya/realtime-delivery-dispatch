"""Live driver locations, held in Redis.

Keys:
  drivers:available:geo     GEO set (sorted set) of AVAILABLE drivers -> used for "nearest driver" search
  driver:{id}               hash: lat, lng, ts, status, active_order
  driver:{id}:heartbeat     expires after DRIVER_HEARTBEAT_TTL_S; missing = driver went silent
"""
import time

from redis.asyncio import Redis

from app.config import settings
from app.services import events

GEO_KEY = "drivers:available:geo"


def driver_key(driver_id: int) -> str:
    return f"driver:{driver_id}"


def heartbeat_key(driver_id: int) -> str:
    return f"driver:{driver_id}:heartbeat"


async def record_location(r: Redis, driver_id: int, lat: float, lng: float) -> None:
    """Called on every location ping (WebSocket or REST). Must be fast: Redis only, no DB."""
    state = await r.hgetall(driver_key(driver_id))

    pipe = r.pipeline()  # one round trip for all commands
    pipe.hset(driver_key(driver_id), mapping={"lat": lat, "lng": lng, "ts": time.time()})
    pipe.set(heartbeat_key(driver_id), 1, ex=settings.driver_heartbeat_ttl_s)
    if state.get("status") == "available":
        pipe.geoadd(GEO_KEY, [lng, lat, str(driver_id)])  # note: Redis GEO takes lng first
    await pipe.execute()

    payload = {"type": "driver.location", "driver_id": driver_id, "lat": lat, "lng": lng,
               "status": state.get("status", "offline")}
    await events.publish(r, events.DISPATCH_CHANNEL, payload)
    # If the driver is on a delivery, the customer tracking that order sees the movement.
    if active := state.get("active_order"):
        await events.publish(r, events.order_channel(int(active)), payload)


async def get_location(r: Redis, driver_id: int) -> tuple[float, float] | None:
    state = await r.hgetall(driver_key(driver_id))
    if "lat" in state and "lng" in state:
        return float(state["lat"]), float(state["lng"])
    return None


async def set_status(r: Redis, driver_id: int, status: str, active_order: int | None = None) -> None:
    """Mirror the driver's DB status into Redis and add/remove them from the GEO pool."""
    pipe = r.pipeline()
    pipe.hset(driver_key(driver_id), "status", status)
    if active_order is not None:
        pipe.hset(driver_key(driver_id), "active_order", active_order)
    else:
        pipe.hdel(driver_key(driver_id), "active_order")
    await pipe.execute()

    if status == "available":
        loc = await get_location(r, driver_id)
        if loc:  # if we don't know where they are yet, the first ping will add them
            await r.geoadd(GEO_KEY, [loc[1], loc[0], str(driver_id)])
    else:
        await remove_from_pool(r, driver_id)


async def remove_from_pool(r: Redis, driver_id: int) -> None:
    await r.zrem(GEO_KEY, str(driver_id))  # a GEO set is a sorted set under the hood


async def is_alive(r: Redis, driver_id: int) -> bool:
    return await r.exists(heartbeat_key(driver_id)) == 1


async def find_nearby(r: Redis, lat: float, lng: float, radius_km: float, count: int) -> list[tuple[int, float]]:
    """Nearest AVAILABLE drivers within radius, closest first: [(driver_id, distance_km), ...]."""
    results = await r.geosearch(
        GEO_KEY, longitude=lng, latitude=lat, radius=radius_km, unit="km",
        sort="ASC", count=count, withdist=True,
    )
    return [(int(member), float(dist)) for member, dist in results]
