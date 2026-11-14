from tests.conftest import NEAR, make_driver, make_order


def test_full_lifecycle_and_audit_trail(client):
    did = make_driver(client, *NEAR)
    oid = make_order(client)["id"]

    r = client.post(f"/orders/{oid}/pickup", params={"driver_id": did})
    assert r.status_code == 200 and r.json()["status"] == "picked_up"
    r = client.post(f"/orders/{oid}/deliver", params={"driver_id": did})
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    assert r.json()["delivered_at"] is not None

    # driver is freed and back in the pool
    assert client.get(f"/drivers/{did}").json()["status"] == "available"
    assert make_order(client)["driver_id"] == did

    history = [e["status"] for e in client.get(f"/orders/{oid}/events").json()]
    assert history == ["pending", "assigned", "picked_up", "delivered"]


def test_invalid_transitions_rejected(client):
    did = make_driver(client, *NEAR)
    oid = make_order(client)["id"]
    # can't deliver before pickup
    assert client.post(f"/orders/{oid}/deliver", params={"driver_id": did}).status_code == 409
    client.post(f"/orders/{oid}/pickup", params={"driver_id": did})
    # can't cancel once picked up
    assert client.post(f"/orders/{oid}/cancel").status_code == 409


def test_wrong_driver_cannot_update_order(client):
    did = make_driver(client, *NEAR)
    other = make_driver(client, *NEAR, available=False)
    oid = make_order(client)["id"]
    assert client.post(f"/orders/{oid}/pickup", params={"driver_id": other}).status_code == 409
    assert client.post(f"/orders/{oid}/pickup", params={"driver_id": did}).status_code == 200


def test_cancel_frees_driver_and_reassigns_waiting_order(client):
    did = make_driver(client, *NEAR)
    first = make_order(client)
    waiting = make_order(client)
    assert waiting["status"] == "pending"

    r = client.post(f"/orders/{first['id']}/cancel", json={"reason": "customer changed mind"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # freed driver immediately picks up the waiting order
    assert client.get(f"/orders/{waiting['id']}").json()["driver_id"] == did


def test_unknown_order_404(client):
    assert client.get("/orders/999").status_code == 404
    assert client.post("/orders/999/cancel").status_code == 404


def test_list_orders_filter_by_status(client):
    make_driver(client, *NEAR)
    make_order(client)
    make_order(client)
    assert len(client.get("/orders", params={"status": "assigned"}).json()) == 1
    assert len(client.get("/orders", params={"status": "pending"}).json()) == 1
