from app.redis_client import redis_holder
from tests.conftest import FAR, NEAR, PASSWORD, make_driver, make_order


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["postgres"] == "ok" and r.json()["redis"] == "ok"


def test_assigns_nearest_driver(client, admin, customer):
    far = make_driver(client, admin, *FAR)
    near = make_driver(client, admin, *NEAR)
    order = make_order(client, customer)
    assert order["status"] == "assigned"
    assert order["driver_id"] == near.id
    assert 0.9 < order["assignment_distance_km"] < 1.1
    assert client.get(f"/drivers/{near.id}", headers=admin.headers).json()["status"] == "busy"
    assert client.get(f"/drivers/{far.id}", headers=admin.headers).json()["status"] == "available"


def test_next_order_goes_to_next_nearest(client, admin, customer):
    far = make_driver(client, admin, *FAR)
    near = make_driver(client, admin, *NEAR)
    assert make_order(client, customer)["driver_id"] == near.id
    # near driver is busy, so the search widens to the 5 km ring
    assert make_order(client, customer)["driver_id"] == far.id


def test_order_waits_when_no_driver_then_assigned_when_one_comes_online(client, admin, customer):
    order = make_order(client, customer)
    assert order["status"] == "pending" and order["driver_id"] is None

    driver = make_driver(client, admin, *NEAR)  # going "available" dispatches waiting orders
    refreshed = client.get(f"/orders/{order['id']}", headers=customer.headers).json()
    assert refreshed["status"] == "assigned" and refreshed["driver_id"] == driver.id


def test_offline_driver_is_never_assigned(client, admin, customer):
    make_driver(client, admin, *NEAR, available=False)
    assert make_order(client, customer)["status"] == "pending"


def test_driver_with_expired_heartbeat_is_skipped(client, admin, customer):
    stale = make_driver(client, admin, *NEAR)
    fresh = make_driver(client, admin, *FAR)
    # Simulate the near driver's app dying: heartbeat key expires
    client.portal.call(redis_holder.client.delete, f"driver:{stale.id}:heartbeat")
    assert make_order(client, customer)["driver_id"] == fresh.id


def test_driver_beyond_max_radius_not_assigned(client, admin, customer):
    make_driver(client, admin, 13.5, 77.5946)  # ~59 km away, beyond the 25 km max ring
    assert make_order(client, customer)["status"] == "pending"


def test_busy_driver_cannot_toggle_status(client, admin, customer):
    driver = make_driver(client, admin, *NEAR)
    make_order(client, customer)
    r = client.post(f"/drivers/{driver.id}/status", json={"status": "offline"}, headers=driver.headers)
    assert r.status_code == 409


def test_validation_rejects_bad_coordinates(client, customer):
    r = client.post("/orders", json={"pickup": {"lat": 200, "lng": 0}, "dropoff": {"lat": 0, "lng": 0}},
                    headers=customer.headers)
    assert r.status_code == 422


def test_duplicate_phone_rejected(client, admin):
    body = {"name": "A", "phone": "+9100000", "password": PASSWORD}
    assert client.post("/drivers", json=body, headers=admin.headers).status_code == 201
    assert client.post("/drivers", json=body, headers=admin.headers).status_code == 409
