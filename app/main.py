"""FastAPI application entrypoint."""
import asyncio
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from app.api import drivers, orders, ws
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.realtime import manager
from app.redis_client import redis_holder
from app.services.dispatch import sweeper_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    async with engine.begin() as conn:
        # Fine for a project; in production you'd run Alembic migrations instead.
        await conn.run_sync(Base.metadata.create_all)
    r = await redis_holder.init()
    await manager.start(r)  # subscribe to Redis Pub/Sub
    sweeper = asyncio.create_task(sweeper_loop(r)) if settings.enable_sweeper else None
    yield
    # --- shutdown ---
    if sweeper:
        sweeper.cancel()
    await manager.stop()
    await redis_holder.close()
    await engine.dispose()


app = FastAPI(title="Real-Time Delivery Dispatch", version="1.0.0", lifespan=lifespan)
app.include_router(drivers.router)
app.include_router(orders.router)
app.include_router(ws.router)


@app.get("/health", tags=["ops"])
async def health():
    """Checks both dependencies; load balancers/orchestrators call this."""
    checks = {}
    try:
        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:  # pragma: no cover
        checks["postgres"] = f"error: {e}"
    try:
        await redis_holder.client.ping()
        checks["redis"] = "ok"
    except Exception as e:  # pragma: no cover
        checks["redis"] = f"error: {e}"
    healthy = all(v == "ok" for v in checks.values())
    body = {"status": "ok" if healthy else "degraded", "instance": socket.gethostname(),
            "websocket_connections": manager.connection_count(), **checks}
    return JSONResponse(body, status_code=200 if healthy else 503)


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC_DIR / "index.html")
