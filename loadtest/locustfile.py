"""Load test: drivers send GPS pings and complete jobs; customers place and check orders.

Headless run (against the Docker stack):
    locust -f loadtest/locustfile.py --host http://localhost:8000 \
        --headless -u 200 -r 20 -t 2m --csv loadtest/results/run

Interactive UI:  locust -f loadtest/locustfile.py --host http://localhost:8000  (open :8089)

"POST /orders [create+dispatch]" is the key number: creating an order runs the full
nearest-driver search + lock + transactional assignment inside the request.
"""
import os
import random
import threading

from locust import HttpUser, between, events, task

CENTER = (12.9716, 77.5946)
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin12345")
PASSWORD = "load-test-pass"

_lock = threading.Lock()
_outcomes = {"assigned": 0, "pending": 0}


def near_center(spread=0.05):
    return CENTER[0] + random.uniform(-spread, spread), CENTER[1] + random.uniform(-spread, spread)


def phone(prefix: str) -> str:
    return f"+{prefix}{random.randint(10**9, 10**10 - 1)}"


class DriverUser(HttpUser):
    """A driver app: location ping every ~1-2 s, picks up and delivers assigned orders."""
    weight = 1
    wait_time = between(1, 2)

    def on_start(self):
        admin = self.client.post("/auth/login", name="/auth/login [admin]",
                                 json={"role": "admin", "username": ADMIN_USER, "password": ADMIN_PASSWORD})
        admin_headers = {"Authorization": f"Bearer {admin.json()['access_token']}"}
        self.phone = phone("91")
        d = self.client.post("/drivers", headers=admin_headers,
                             json={"name": "Load Driver", "phone": self.phone, "password": PASSWORD})
        self.id = d.json()["id"]
        tok = self.client.post("/auth/login", name="/auth/login [driver]",
                               json={"role": "driver", "username": self.phone, "password": PASSWORD})
        self.h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
        self.pos = near_center()
        self.ping()
        self.client.post(f"/drivers/{self.id}/status", name="/drivers/{id}/status",
                         json={"status": "available"}, headers=self.h)

    @task(5)
    def ping(self):
        self.pos = (self.pos[0] + random.uniform(-5e-4, 5e-4), self.pos[1] + random.uniform(-5e-4, 5e-4))
        self.client.post(f"/drivers/{self.id}/location", name="/drivers/{id}/location",
                         json={"lat": self.pos[0], "lng": self.pos[1]}, headers=self.h)

    @task(1)
    def work_assigned_orders(self):
        r = self.client.get("/orders?status=assigned", name="/orders [driver: assigned]", headers=self.h)
        for order in r.json():
            self.client.post(f"/orders/{order['id']}/pickup", name="/orders/{id}/pickup", headers=self.h)
            self.client.post(f"/orders/{order['id']}/deliver", name="/orders/{id}/deliver", headers=self.h)


class CustomerUser(HttpUser):
    """A customer: places an order every few seconds and checks their order list."""
    weight = 1
    wait_time = between(2, 4)

    def on_start(self):
        r = self.client.post("/auth/customers/register",
                             json={"name": "Load Customer", "phone": phone("92"), "password": PASSWORD})
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    @task(1)
    def place_order(self):
        pickup, dropoff = near_center(), near_center()
        with self.client.post("/orders", name="POST /orders [create+dispatch]", headers=self.h,
                              json={"pickup": {"lat": pickup[0], "lng": pickup[1]},
                                    "dropoff": {"lat": dropoff[0], "lng": dropoff[1]}},
                              catch_response=True) as resp:
            if resp.status_code != 201:
                resp.failure(f"status {resp.status_code}")
                return
            status = resp.json()["status"]
            with _lock:
                _outcomes["assigned" if status == "assigned" else "pending"] += 1

    @task(2)
    def check_orders(self):
        self.client.get("/orders?limit=20", name="/orders [customer: mine]", headers=self.h)


@events.quitting.add_listener
def _report(environment, **_):
    total = _outcomes["assigned"] + _outcomes["pending"]
    if total:
        pct = 100 * _outcomes["assigned"] / total
        print(f"\nOrders created: {total} | assigned instantly: {_outcomes['assigned']} ({pct:.1f}%)"
              f" | queued for retry: {_outcomes['pending']}")
