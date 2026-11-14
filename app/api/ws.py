"""WebSocket endpoints.

/ws/drivers/{id}   driver app: SENDS location pings, RECEIVES assignments
/ws/orders/{id}    customer:   RECEIVES order status + live driver location
/ws/dispatch       ops dashboard: RECEIVES every event
"""
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.config import settings
from app.db import SessionLocal
from app.models import Driver, Order
from app.realtime import manager
from app.redis_client import get_redis
from app.schemas import LocationMessage, OrderOut
from app.services import events, location
from app.services.drivers import persist_location

router = APIRouter(tags=["websockets"])
log = logging.getLogger(__name__)


@router.websocket("/ws/drivers/{driver_id}")
async def driver_socket(ws: WebSocket, driver_id: int):
    r = get_redis()
    async with SessionLocal() as session:
        driver = await session.get(Driver, driver_id)
    if driver is None:
        await ws.close(code=4404)
        return

    await ws.accept()
    channel = events.driver_channel(driver_id)
    manager.connect(channel, ws)
    await ws.send_json({"type": "connected", "driver_id": driver_id, "status": driver.status.value})
    last_persist = time.monotonic()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = LocationMessage.model_validate(json.loads(raw))
            except (ValueError, ValidationError) as e:
                await ws.send_json({"type": "error", "detail": str(e)[:200]})
                continue
            await location.record_location(r, driver_id, msg.lat, msg.lng)
            # Throttled write-behind: Redis gets every ping, Postgres gets one every 30 s.
            if time.monotonic() - last_persist > settings.location_persist_interval_s:
                async with SessionLocal() as session:
                    await persist_location(session, r, driver_id)
                last_persist = time.monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(channel, ws)
        try:
            async with SessionLocal() as session:
                await persist_location(session, r, driver_id)
        except Exception:
            log.exception("failed to persist location on disconnect")


@router.websocket("/ws/orders/{order_id}")
async def order_socket(ws: WebSocket, order_id: int):
    r = get_redis()
    async with SessionLocal() as session:
        order = await session.get(Order, order_id)
    if order is None:
        await ws.close(code=4404)
        return

    await ws.accept()
    channel = events.order_channel(order_id)
    manager.connect(channel, ws)
    # Send a snapshot first so the client has state before any live updates arrive.
    snapshot = {"type": "snapshot", "order": OrderOut.model_validate(order).model_dump(mode="json")}
    if order.driver_id and (loc := await location.get_location(r, order.driver_id)):
        snapshot["driver_location"] = {"lat": loc[0], "lng": loc[1]}
    await ws.send_json(snapshot)
    try:
        while True:
            await ws.receive_text()  # we only push; reading detects disconnects
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(channel, ws)


@router.websocket("/ws/dispatch")
async def dispatch_socket(ws: WebSocket):
    await ws.accept()
    manager.connect(events.DISPATCH_CHANNEL, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(events.DISPATCH_CHANNEL, ws)
