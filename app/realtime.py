"""WebSocket connection manager + Redis Pub/Sub listener (one per API process)."""
import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.services.events import PREFIX

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # channel name -> sockets on THIS process listening to it
        self.channels: dict[str, set[WebSocket]] = defaultdict(set)
        self._pubsub: PubSub | None = None
        self._task: asyncio.Task | None = None

    def connect(self, channel: str, ws: WebSocket) -> None:
        self.channels[channel].add(ws)

    def disconnect(self, channel: str, ws: WebSocket) -> None:
        self.channels[channel].discard(ws)
        if not self.channels[channel]:
            del self.channels[channel]

    def connection_count(self) -> int:
        return sum(len(s) for s in self.channels.values())

    async def broadcast_local(self, channel: str, message: str) -> None:
        for ws in list(self.channels.get(channel, ())):
            try:
                await ws.send_text(message)
            except Exception:
                # Client vanished; drop it instead of crashing the listener.
                self.disconnect(channel, ws)

    async def start(self, r: Redis) -> None:
        # Subscribe BEFORE returning so no event published after startup is missed.
        self._pubsub = r.pubsub()
        await self._pubsub.psubscribe(PREFIX + "*")
        self._task = asyncio.create_task(self._reader(), name="pubsub-reader")

    async def _reader(self) -> None:
        assert self._pubsub is not None
        while True:
            try:
                msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "pmessage":
                    channel = msg["channel"].removeprefix(PREFIX)
                    await self.broadcast_local(channel, msg["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pubsub reader error; retrying")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.aclose()


manager = ConnectionManager()
