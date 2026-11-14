from app.redis_client import redis_holder
from tests.conftest import FAR, NEAR, make_driver, make_order


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["postgres"] == "ok" and r.json()["redis"] == "ok"


def test_assigns_nearest_driver(client):
    far = make_driver(client, *FAR)
    near = make_driver(client, *NEAR)
    order = make_order(client)
    assert order["status"] == "assigned"
    assert order["driver_id"] == near
    assert 0.9 < order["assignment_distance_km"] < 1.1
    assert client.get(f"/drivers/{near}").json()["status"] == "busy"
    assert client.get(f"/drivers/{far}").json()["status"] == "available"


def test_next_order_goes_to_next_nearest(client):
    far = make_driver(client, *FAR)
    near = make_driver(client, *NEAR)
    assert make_order(client)["driver_id"] == near
    second = make_order(client)
    assert second["driver_id"] == far  # near driver is busy, so search widens to the 5 km ring


def test_order_waits_when_no_driver_then_assigned_when_one_comes_online(client):
    order = make_order(client)
    assert order["status"] == "pending" and order["driver_id"] is None

    did = make_driver(client, *NEAR)  # going "available" triggers dispatch of waiting orders
    refreshed = client.get(f"/orders/{order['id']}").json()
    assert refreshed["status"] == "assigned" and refreshed["driver_id"] == did


def test_offline_driver_is_never_assigned(client):
    make_driver(client, *NEAR, available=False)
    assert make_order(client)["status"] == "pending"


def test_driver_with_expired_heartbeat_is_skipped(client):
    stale = make_driver(client, *NEAR)
    fresh = make_driver(client, *FAR)
    # Simulate the near driver's app dying: heartbeat key expires
    client.portal.call(redis_holder.client.delete, f"driver:{stale}:heartbeat")
    order = make_order(client)
    assert order["driver_id"] == fresh


def test_driver_beyond_max_radius_not_assigned(client):
    make_driver(client, 13.5, 77.5946)  # ~59 km away, beyond the 25 km max ring
    assert make_order(client)["status"] == "pending"


def test_busy_driver_cannot_toggle_status(client):
    did = make_driver(client, *NEAR)
    make_order(client)
    r = client.post(f"/drivers/{did}/status", json={"status": "offline"})
    assert r.status_code == 409


def test_validation_rejects_bad_coordinates(client):
    r = client.post("/orders", json={"customer_name": "X", "pickup": {"lat": 200, "lng": 0},
                                     "dropoff": {"lat": 0, "lng": 0}})
    assert r.status_code == 422


def test_duplicate_phone_rejected(client):
    assert client.post("/drivers", json={"name": "A", "phone": "+9100000"}).status_code == 201
    assert client.post("/drivers", json={"name": "B", "phone": "+9100000"}).status_code == 409
