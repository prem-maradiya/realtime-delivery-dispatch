from tests.conftest import NEAR, make_driver, make_order


def test_full_lifecycle_and_audit_trail(client, admin, customer):
    driver = make_driver(client, admin, *NEAR)
    oid = make_order(client, customer)["id"]

    r = client.post(f"/orders/{oid}/pickup", headers=driver.headers)
    assert r.status_code == 200 and r.json()["status"] == "picked_up"
    r = client.post(f"/orders/{oid}/deliver", headers=driver.headers)
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    assert r.json()["delivered_at"] is not None

    # driver is freed and back in the pool
    assert client.get(f"/drivers/{driver.id}", headers=driver.headers).json()["status"] == "available"
    assert make_order(client, customer)["driver_id"] == driver.id

    history = [e["status"] for e in client.get(f"/orders/{oid}/events", headers=customer.headers).json()]
    assert history == ["pending", "assigned", "picked_up", "delivered"]


def test_invalid_transitions_rejected(client, admin, customer):
    driver = make_driver(client, admin, *NEAR)
    oid = make_order(client, customer)["id"]
    # can't deliver before pickup
    assert client.post(f"/orders/{oid}/deliver", headers=driver.headers).status_code == 409
    client.post(f"/orders/{oid}/pickup", headers=driver.headers)
    # can't cancel once picked up
    assert client.post(f"/orders/{oid}/cancel", headers=customer.headers).status_code == 409


def test_cancel_frees_driver_and_reassigns_waiting_order(client, admin, customer):
    driver = make_driver(client, admin, *NEAR)
    first = make_order(client, customer)
    waiting = make_order(client, customer)
    assert waiting["status"] == "pending"

    r = client.post(f"/orders/{first['id']}/cancel", json={"reason": "customer changed mind"},
                    headers=customer.headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # freed driver immediately picks up the waiting order
    assert client.get(f"/orders/{waiting['id']}", headers=customer.headers).json()["driver_id"] == driver.id


def test_admin_can_cancel_any_order(client, customer, admin):
    oid = make_order(client, customer)["id"]
    r = client.post(f"/orders/{oid}/cancel", headers=admin.headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_unknown_order_404(client, admin):
    assert client.get("/orders/999", headers=admin.headers).status_code == 404
    assert client.post("/orders/999/cancel", headers=admin.headers).status_code == 404


def test_list_orders_filter_by_status(client, admin, customer):
    make_driver(client, admin, *NEAR)
    make_order(client, customer)
    make_order(client, customer)
    assert len(client.get("/orders", params={"status": "assigned"}, headers=admin.headers).json()) == 1
    assert len(client.get("/orders", params={"status": "pending"}, headers=admin.headers).json()) == 1
