# Real-Time Delivery Dispatch System

[![CI](https://github.com/prem-maradiya/realtime-delivery-dispatch/actions/workflows/ci.yml/badge.svg)](https://github.com/prem-maradiya/realtime-delivery-dispatch/actions/workflows/ci.yml)

Matches delivery orders to the **nearest available driver** in milliseconds and streams
live status + driver location to customers, drivers and an ops dashboard.

**Stack:** FastAPI (async Python) · PostgreSQL · Redis (GEO, Pub/Sub, locks) · WebSockets · Docker Compose · Nginx

## Features

- **Nearest-driver dispatch** using Redis `GEOSEARCH` with expanding search radii (2 → 5 → 10 → 25 km)
- **No double assignment** under concurrent requests across multiple API replicas
- **Live tracking** over WebSockets for customers, drivers and an operations dashboard
- **Horizontally scalable**: stateless API replicas behind Nginx, events fanned out via Redis Pub/Sub
- **Order state machine** with an append-only audit trail
- **Resilience**: driver heartbeats, background retry of pending orders, health checks
- **Security**: JWT authentication with admin / driver / customer roles; every endpoint and WebSocket is access-controlled
- **Schema migrations** with Alembic, applied by a one-shot service before the API starts
- **CI** on every push: test suite + Docker image build

## Architecture

```
 Customer / Driver / Dashboard  ──WebSocket + HTTP──►  Nginx  ──►  API replicas (FastAPI)
                                                                       │        │
                                                     PostgreSQL ◄──────┘        └──────► Redis
                                                (orders, drivers,              (GEO index, live locations,
                                                 audit trail)                   heartbeats, locks, Pub/Sub)
```

**PostgreSQL is the source of truth** for orders, drivers and history. **Redis holds hot,
rebuildable state**: driver locations change every few seconds, so every ping goes to Redis
and is persisted to Postgres only periodically (write-behind).

## Design decisions

**Dispatch and concurrency.** For each order, the engine searches the GEO index closest-first.
For each candidate it:
1. skips drivers whose heartbeat key has expired (app closed or offline),
2. acquires a short-lived Redis lock on the driver (`SET NX EX`, released by a compare-and-delete Lua script),
3. runs two conditional updates in a single transaction:
   `drivers SET busy WHERE status='available'` and `orders SET assigned WHERE status='pending'`.

If either update affects no rows, the transaction rolls back and the next driver is tried.
The lock reduces contention between replicas; the conditional update is what guarantees
correctness, even if Redis state is stale.

**Real-time fan-out.** A WebSocket is bound to one API process. Every process subscribes to
`events:*` in Redis and forwards messages to its own connected sockets, so an event raised on
any replica reaches clients connected to any other replica.

**Authentication and access control.** `POST /auth/login` returns a signed JWT (HS256)
carrying the user's role and id. Passwords are hashed with PBKDF2-SHA256 and a per-user salt.

| Role | Can do |
|---|---|
| `admin` | onboard drivers, list all drivers and orders, cancel any order, open the ops dashboard |
| `driver` | update own status and location, pick up and deliver orders assigned to them |
| `customer` | self-register, create orders, view, track and cancel their own orders |

A driver's identity always comes from the token, never from request parameters. Other users'
orders return `404` rather than `403`, so order ids can't be probed. WebSockets authenticate
with `?token=` (browsers can't set headers on a WebSocket) and close with `4401`, `4403` or `4404`.
Because query strings can appear in proxy and access logs, a production deployment would
issue a short-lived, single-use ticket for the WebSocket handshake instead of the main token.

**Order lifecycle.**

```
pending → assigned → picked_up → delivered
   └──────────┴──→ cancelled
```

Invalid transitions return `409 Conflict`. Delivering or cancelling releases the driver and
immediately dispatches the oldest waiting orders.

## Project structure

```
app/
  main.py            app lifecycle, health check, dashboard
  models.py          SQLAlchemy models + state machine
  schemas.py         Pydantic request/response models
  realtime.py        WebSocket manager + Redis Pub/Sub listener
  security.py        password hashing, JWT, role checks
  api/               REST and WebSocket routes
  services/          dispatch engine, locations, locks, order and driver logic
migrations/          Alembic schema migrations
tests/               dispatch, lifecycle, auth, WebSocket, concurrency and migration tests
static/index.html    live map dashboard
scripts/simulate.py  traffic simulator
loadtest/            Locust load test
```

## Run it (Docker)

```bash
docker compose up --build
```

- Dashboard (live map): http://localhost:8000 (sign in with the admin account)
- API docs (Swagger):  http://localhost:8000/docs (use **Authorize** with a token from `/auth/login`)
- Health:              http://localhost:8000/health

The default admin login is `admin` / `admin12345` for local development only. For anything
shared, copy `.env.example` to `.env` and set `JWT_SECRET` and `ADMIN_PASSWORD`.

Run 3 API replicas behind Nginx (proves the Pub/Sub fan-out works across instances):

```bash
docker compose up --build --scale api=3
```

## Generate traffic

```bash
pip install -r requirements-dev.txt
python scripts/simulate.py --drivers 10 --order-every 2 --duration 120
```

## Tests (no Docker needed: SQLite + in-memory Redis)

```bash
pip install -r requirements-dev.txt
pytest -v
```

## API

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/auth/customers/register` | public | Customer sign-up, returns token |
| POST | `/auth/login` | public | Get a JWT for any role |
| POST | `/drivers` | admin | Onboard driver |
| GET | `/drivers` | admin | List drivers (with live Redis location) |
| GET | `/drivers/{id}` | admin, self | Driver detail |
| POST | `/drivers/{id}/status` | admin, self | Go `available` / `offline` |
| POST | `/drivers/{id}/location` | self | REST location ping (fallback) |
| POST | `/orders` | customer | Create order → auto-dispatch |
| GET | `/orders?status=` | any | List orders (scoped to the caller) |
| GET | `/orders/{id}` | owner, assigned driver, admin | Order detail |
| GET | `/orders/{id}/events` | owner, assigned driver, admin | Audit trail |
| POST | `/orders/{id}/pickup` | assigned driver | Picked up |
| POST | `/orders/{id}/deliver` | assigned driver | Delivered |
| POST | `/orders/{id}/cancel` | owner, admin | Cancel |
| WS | `/ws/drivers/{id}?token=` | self | Send location, receive assignments |
| WS | `/ws/orders/{id}?token=` | owner, assigned driver, admin | Live order tracking |
| WS | `/ws/dispatch?token=` | admin | Every event |
| GET | `/health` | public | Postgres + Redis health |

## Database migrations

```bash
alembic upgrade head                              # apply
alembic revision --autogenerate -m "add column"   # create after changing models
```

In Docker, the `migrate` service runs `alembic upgrade head` once, and the API replicas start
only after it succeeds.

## Roadmap

- Driver accept/reject with timeout-based reassignment
- Refresh tokens and token revocation
- Road-network ETA instead of straight-line distance; batch matching
- Prometheus metrics (dispatch latency, pending queue depth)
