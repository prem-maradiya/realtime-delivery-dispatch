"""WebSocket + Redis Pub/Sub end-to-end."""
from tests.conftest import NEAR, make_driver, make_order


def test_customer_sees_assignment_and_live_driver_location(client):
    order = make_order(client)  # pending: no drivers yet
    with client.websocket_connect(f"/ws/orders/{order['id']}") as customer:
        snap = customer.receive_json()
        assert snap["type"] == "snapshot" and snap["order"]["status"] == "pending"

        did = make_driver(client, *NEAR)  # comes online -> order dispatched
        msgs = [customer.receive_json()]
        while msgs[-1]["type"] != "order.assigned":
            msgs.append(customer.receive_json())
        assert msgs[-1]["order"]["driver_id"] == did

        # Driver moves: the customer tracking the order receives it
        with client.websocket_connect(f"/ws/drivers/{did}") as driver:
            assert driver.receive_json()["type"] == "connected"
            driver.send_json({"type": "location", "lat": 12.975, "lng": 77.5946})
            ev = customer.receive_json()
            assert ev["type"] == "driver.location" and ev["lat"] == 12.975


def test_driver_receives_assignment_over_websocket(client):
    did = make_driver(client, *NEAR)
    with client.websocket_connect(f"/ws/drivers/{did}") as driver:
        assert driver.receive_json()["type"] == "connected"
        order = make_order(client)
        ev = driver.receive_json()
        assert ev["type"] == "order.assigned" and ev["order"]["id"] == order["id"]


def test_dashboard_receives_all_events(client):
    with client.websocket_connect("/ws/dispatch") as dash:
        did = make_driver(client, *NEAR)
        make_order(client)
        types = set()
        while "order.assigned" not in types:
            types.add(dash.receive_json()["type"])
        assert {"driver.location", "driver.status_changed", "order.created", "order.assigned"} <= types
        assert did


def test_driver_socket_rejects_bad_messages(client):
    did = make_driver(client, *NEAR)
    with client.websocket_connect(f"/ws/drivers/{did}") as driver:
        driver.receive_json()
        driver.send_text("not json")
        assert driver.receive_json()["type"] == "error"
        driver.send_json({"type": "location", "lat": 999, "lng": 0})
        assert driver.receive_json()["type"] == "error"
