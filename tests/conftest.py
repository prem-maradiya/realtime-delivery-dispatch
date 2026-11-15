"""Tests run with SQLite + in-memory fake Redis, so no Docker is needed for `pytest`."""
import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Outside the project folder so OneDrive/antivirus don't lock it
TEST_DB = Path(tempfile.gettempdir()) / "dispatch_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["ENABLE_SWEEPER"] = "false"           # tests trigger dispatch explicitly
os.environ["PASSWORD_HASH_ITERATIONS"] = "1000"  # fast hashing in tests only

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
PASSWORD = "s3cret-pass"


@dataclass
class Account:
    id: int | None
    token: str

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture
def client():
    asyncio.run(reset_db())  # fresh, empty tables for every test
    with TestClient(app) as c:  # runs startup/shutdown (connects Redis, starts Pub/Sub listener)
        yield c


@pytest.fixture
def admin(client) -> Account:
    r = client.post("/auth/login", json={"role": "admin", "username": "admin", "password": "admin12345"})
    assert r.status_code == 200, r.text
    return Account(None, r.json()["access_token"])


@pytest.fixture
def customer(client) -> Account:
    return make_customer(client)


_phone = iter(range(1000000, 9999999))


def make_customer(client) -> Account:
    r = client.post("/auth/customers/register",
                    json={"name": "Asha", "phone": f"+91{next(_phone)}", "password": PASSWORD})
    assert r.status_code == 201, r.text
    return Account(r.json()["id"], r.json()["access_token"])


def make_driver(client, admin: Account, lat=None, lng=None, available=True) -> Account:
    phone = f"+91{next(_phone)}"
    d = client.post("/drivers", json={"name": "Test Driver", "phone": phone, "password": PASSWORD},
                    headers=admin.headers)
    assert d.status_code == 201, d.text
    login = client.post("/auth/login", json={"role": "driver", "username": phone, "password": PASSWORD})
    assert login.status_code == 200, login.text
    acct = Account(d.json()["id"], login.json()["access_token"])
    if lat is not None:
        r = client.post(f"/drivers/{acct.id}/location", json={"lat": lat, "lng": lng}, headers=acct.headers)
        assert r.status_code == 204, r.text
    if available:
        r = client.post(f"/drivers/{acct.id}/status", json={"status": "available"}, headers=acct.headers)
        assert r.status_code == 200, r.text
    return acct


def make_order(client, customer: Account) -> dict:
    r = client.post("/orders", json={"pickup": PICKUP, "dropoff": DROPOFF}, headers=customer.headers)
    assert r.status_code == 201, r.text
    return r.json()
