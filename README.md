# Real-Time Delivery Dispatch System

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
  api/               REST and WebSocket routes
  services/          dispatch engine, locations, locks, order and driver logic
tests/               dispatch, lifecycle, WebSocket and concurrency tests
static/index.html    live map dashboard
scripts/simulate.py  traffic simulator
```

## Run it (Docker)

```bash
docker compose up --build
```

- Dashboard (live map): http://localhost:8000
- API docs (Swagger):  http://localhost:8000/docs
- Health:              http://localhost:8000/health

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

| Method | Path | Purpose |
|---|---|---|
| POST | `/drivers` | Register driver |
| GET | `/drivers` | List drivers (with live Redis location) |
| POST | `/drivers/{id}/status` | Go `available` / `offline` |
| POST | `/drivers/{id}/location` | REST location ping (fallback) |
| POST | `/orders` | Create order → auto-dispatch |
| GET | `/orders?status=` | List orders |
| GET | `/orders/{id}` | Order detail |
| GET | `/orders/{id}/events` | Audit trail |
| POST | `/orders/{id}/pickup?driver_id=` | Driver picked up |
| POST | `/orders/{id}/deliver?driver_id=` | Driver delivered |
| POST | `/orders/{id}/cancel` | Cancel |
| WS | `/ws/drivers/{id}` | Driver: send location, receive assignments |
| WS | `/ws/orders/{id}` | Customer: live order tracking |
| WS | `/ws/dispatch` | Ops dashboard: every event |
| GET | `/health` | Postgres + Redis health |

## Roadmap

- JWT authentication for drivers and customers
- Alembic migrations
- Road-network ETA instead of straight-line distance; batch matching
- Reassignment when a driver doesn't pick up within a time limit
- Prometheus metrics (dispatch latency, pending queue depth)
