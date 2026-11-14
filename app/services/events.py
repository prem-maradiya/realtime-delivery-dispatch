"""Publishing real-time events through Redis Pub/Sub.

Every API replica publishes to Redis; every replica is subscribed to Redis and
forwards events to the WebSockets connected to *it*. So a driver connected to
replica A can be assigned an order created on replica B.
"""
import json
from typing import Any

from redis.asyncio import Redis

PREFIX = "events:"
DISPATCH_CHANNEL = "dispatch"  # operations dashboard: sees everything


def order_channel(order_id: int) -> str:
    return f"order:{order_id}"


def driver_channel(driver_id: int) -> str:
    return f"driver:{driver_id}"


async def publish(r: Redis, channel: str, event: dict[str, Any]) -> None:
    await r.publish(PREFIX + channel, json.dumps(event, default=str))
