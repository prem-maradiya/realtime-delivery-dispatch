"""WebSocket + Redis Pub/Sub end-to-end."""
from tests.conftest import NEAR, make_driver, make_order


def test_customer_sees_assignment_and_live_driver_location(client, admin, customer):
    order = make_order(client, customer)  # pending: no drivers yet
    with client.websocket_connect(f"/ws/orders/{order['id']}?token={customer.token}") as cust_ws:
        snap = cust_ws.receive_json()
        assert snap["type"] == "snapshot" and snap["order"]["status"] == "pending"

        driver = make_driver(client, admin, *NEAR)  # comes online -> order dispatched
        msgs = [cust_ws.receive_json()]
        while msgs[-1]["type"] != "order.assigned":
            msgs.append(cust_ws.receive_json())
        assert msgs[-1]["order"]["driver_id"] == driver.id

        # Driver moves: the customer tracking the order receives it
        with client.websocket_connect(f"/ws/drivers/{driver.id}?token={driver.token}") as drv_ws:
            assert drv_ws.receive_json()["type"] == "connected"
            drv_ws.send_json({"type": "location", "lat": 12.975, "lng": 77.5946})
            ev = cust_ws.receive_json()
            assert ev["type"] == "driver.location" and ev["lat"] == 12.975


def test_driver_receives_assignment_over_websocket(client, admin, customer):
    driver = make_driver(client, admin, *NEAR)
    with client.websocket_connect(f"/ws/drivers/{driver.id}?token={driver.token}") as ws:
        assert ws.receive_json()["type"] == "connected"
        order = make_order(client, customer)
        ev = ws.receive_json()
        assert ev["type"] == "order.assigned" and ev["order"]["id"] == order["id"]


def test_dashboard_receives_all_events(client, admin, customer):
    with client.websocket_connect(f"/ws/dispatch?token={admin.token}") as dash:
        make_driver(client, admin, *NEAR)
        make_order(client, customer)
        types = set()
        while "order.assigned" not in types:
            types.add(dash.receive_json()["type"])
        assert {"driver.location", "driver.status_changed", "order.created", "order.assigned"} <= types


def test_driver_socket_rejects_bad_messages(client, admin):
    driver = make_driver(client, admin, *NEAR)
    with client.websocket_connect(f"/ws/drivers/{driver.id}?token={driver.token}") as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "location", "lat": 999, "lng": 0})
        assert ws.receive_json()["type"] == "error"
