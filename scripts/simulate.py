"""Traffic simulator: fake drivers drive around, customers place orders.

Usage:  python scripts/simulate.py --drivers 10 --order-every 3 --duration 120
Then open http://localhost:8000 to watch the dashboard.
"""
import argparse
import asyncio
import json
import math
import random

import httpx
import websockets

CENTER = (12.9716, 77.5946)  # any city centre works
STEP_DEG = 0.0006            # ~65 m per tick (sped up for the demo)
TICK_S = 0.5


def near_center(spread=0.03):
    return CENTER[0] + random.uniform(-spread, spread), CENTER[1] + random.uniform(-spread, spread)


def step_towards(pos, target):
    dlat, dlng = target[0] - pos[0], target[1] - pos[1]
    dist = math.hypot(dlat, dlng)
    if dist <= STEP_DEG:
        return target, True
    return (pos[0] + dlat / dist * STEP_DEG, pos[1] + dlng / dist * STEP_DEG), False


async def drive(ws, pos, target):
    while True:
        pos, arrived = step_towards(pos, target)
        await ws.send(json.dumps({"type": "location", "lat": pos[0], "lng": pos[1]}))
        if arrived:
            return pos
        await asyncio.sleep(TICK_S)


async def run_driver(base, http, idx, stop):
    tag = random.randint(10000, 99999)
    d = (await http.post("/drivers", json={"name": f"Driver {idx}", "phone": f"+91{tag}{idx:04d}"})).json()
    did, pos = d["id"], near_center()
    ws_url = base.replace("http", "ws", 1) + f"/ws/drivers/{did}"
    jobs: asyncio.Queue = asyncio.Queue()

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "location", "lat": pos[0], "lng": pos[1]}))
        await http.post(f"/drivers/{did}/status", json={"status": "available"})

        async def reader():
            async for raw in ws:
                ev = json.loads(raw)
                if ev.get("type") == "order.assigned":
                    await jobs.put(ev["order"])

        reader_task = asyncio.create_task(reader())
        try:
            while not stop.is_set():
                try:
                    order = jobs.get_nowait()
                except asyncio.QueueEmpty:
                    # idle: wander a little so the dashboard looks alive
                    pos = (pos[0] + random.uniform(-1, 1) * STEP_DEG / 3, pos[1] + random.uniform(-1, 1) * STEP_DEG / 3)
                    await ws.send(json.dumps({"type": "location", "lat": pos[0], "lng": pos[1]}))
                    await asyncio.sleep(TICK_S * 2)
                    continue
                oid = order["id"]
                print(f"driver {did}: heading to pickup for order {oid}")
                pos = await drive(ws, pos, (order["pickup_lat"], order["pickup_lng"]))
                await http.post(f"/orders/{oid}/pickup", params={"driver_id": did})
                pos = await drive(ws, pos, (order["dropoff_lat"], order["dropoff_lng"]))
                await http.post(f"/orders/{oid}/deliver", params={"driver_id": did})
                print(f"driver {did}: delivered order {oid}")
        finally:
            reader_task.cancel()


async def run_customers(http, every, stop):
    n = 0
    while not stop.is_set():
        n += 1
        o = (await http.post("/orders", json={
            "customer_name": f"Customer {n}",
            "pickup": dict(zip(("lat", "lng"), near_center())),
            "dropoff": dict(zip(("lat", "lng"), near_center(0.04))),
        })).json()
        print(f"order {o['id']} created -> {o['status']}" + (f" (driver {o['driver_id']})" if o["driver_id"] else ""))
        await asyncio.sleep(every)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--drivers", type=int, default=8)
    p.add_argument("--order-every", type=float, default=3.0)
    p.add_argument("--duration", type=float, default=120)
    a = p.parse_args()

    stop = asyncio.Event()
    async with httpx.AsyncClient(base_url=a.base, timeout=10) as http:
        tasks = [asyncio.create_task(run_driver(a.base, http, i, stop)) for i in range(a.drivers)]
        tasks.append(asyncio.create_task(run_customers(http, a.order_every, stop)))
        await asyncio.sleep(a.duration)
        stop.set()
        await asyncio.sleep(1)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    print("simulation finished")


if __name__ == "__main__":
    asyncio.run(main())
