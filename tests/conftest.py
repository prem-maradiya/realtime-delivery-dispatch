"""Tests run with SQLite + in-memory fake Redis, so no Docker is needed for `pytest`."""
import asyncio
import os
import tempfile
from pathlib import Path

# Outside the project folder so OneDrive/antivirus don't lock it
TEST_DB = Path(tempfile.gettempdir()) / "dispatch_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["ENABLE_SWEEPER"] = "false"  # tests trigger dispatch explicitly

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


async def reset_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

# Two points ~1 km and ~4 km from the pickup used in tests
PICKUP = {"lat": 12.9716, "lng": 77.5946}
NEAR = (12.9806, 77.5946)   # ~1.0 km north
FAR = (13.0076, 77.5946)    # ~4.0 km north
DROPOFF = {"lat": 12.95, "lng": 77.60}


@pytest.fixture
def client():
    asyncio.run(reset_db())  # fresh, empty tables for every test
    with TestClient(app) as c:  # runs startup/shutdown (connects Redis, starts Pub/Sub listener)
        yield c


_phone = iter(range(1000000, 9999999))


def make_driver(client, lat=None, lng=None, available=True) -> int:
    d = client.post("/drivers", json={"name": "Test Driver", "phone": f"+91{next(_phone)}"})
    assert d.status_code == 201, d.text
    did = d.json()["id"]
    if lat is not None:
        assert client.post(f"/drivers/{did}/location", json={"lat": lat, "lng": lng}).status_code == 204
    if available:
        assert client.post(f"/drivers/{did}/status", json={"status": "available"}).status_code == 200
    return did


def make_order(client):
    r = client.post("/orders", json={"customer_name": "Asha", "pickup": PICKUP, "dropoff": DROPOFF})
    assert r.status_code == 201, r.text
    return r.json()
