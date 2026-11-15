"""Authentication and authorisation: who is allowed to do what."""
import pytest
from starlette.websockets import WebSocketDisconnect

from app.security import create_token, hash_password, verify_password
from tests.conftest import NEAR, PASSWORD, make_customer, make_driver, make_order


def test_password_hashing():
    h = hash_password("correct horse")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert hash_password("correct horse") != h  # random salt each time


def test_login_success_and_failure(client, customer):
    assert client.post("/auth/login", json={"role": "admin", "username": "admin",
                                            "password": "nope"}).status_code == 401
    assert client.post("/auth/login", json={"role": "customer", "username": "+910",
                                            "password": PASSWORD}).status_code == 401


def test_requests_without_or_with_bad_token_rejected(client):
    assert client.get("/orders").status_code == 401
    assert client.get("/orders", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_customer_cannot_onboard_drivers_or_list_them(client, customer):
    body = {"name": "X", "phone": "+9112345", "password": PASSWORD}
    assert client.post("/drivers", json=body, headers=customer.headers).status_code == 403
    assert client.get("/drivers", headers=customer.headers).status_code == 403


def test_only_customers_create_orders(client, admin):
    driver = make_driver(client, admin, *NEAR)
    body = {"pickup": {"lat": 12.97, "lng": 77.59}, "dropoff": {"lat": 12.95, "lng": 77.6}}
    assert client.post("/orders", json=body, headers=driver.headers).status_code == 403
    assert client.post("/orders", json=body, headers=admin.headers).status_code == 403


def test_customer_cannot_see_or_cancel_another_customers_order(client, customer):
    oid = make_order(client, customer)["id"]
    other = make_customer(client)
    assert client.get(f"/orders/{oid}", headers=other.headers).status_code == 404
    assert client.post(f"/orders/{oid}/cancel", headers=other.headers).status_code == 404
    assert client.get("/orders", headers=other.headers).json() == []


def test_driver_cannot_act_on_another_drivers_order(client, admin, customer):
    assigned = make_driver(client, admin, *NEAR)
    other = make_driver(client, admin, *NEAR, available=False)
    oid = make_order(client, customer)["id"]
    assert client.post(f"/orders/{oid}/pickup", headers=other.headers).status_code == 409
    assert client.get(f"/orders/{oid}", headers=other.headers).status_code == 404
    assert client.post(f"/orders/{oid}/pickup", headers=assigned.headers).status_code == 200


def test_driver_cannot_change_another_drivers_status_or_location(client, admin):
    a = make_driver(client, admin, *NEAR)
    b = make_driver(client, admin, *NEAR)
    assert client.post(f"/drivers/{b.id}/status", json={"status": "offline"},
                       headers=a.headers).status_code == 403
    assert client.post(f"/drivers/{b.id}/location", json={"lat": 1, "lng": 1},
                       headers=a.headers).status_code == 403


def test_expired_token_rejected(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "jwt_expire_minutes", -1)
    token = create_token("admin", None)
    assert client.get("/drivers", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_websockets_require_valid_token(client, admin, customer):
    driver = make_driver(client, admin, *NEAR)
    oid = make_order(client, customer)["id"]
    other = make_customer(client)
    cases = [
        ("/ws/dispatch", 4401),                                   # no token
        (f"/ws/dispatch?token={customer.token}", 4403),           # wrong role
        (f"/ws/drivers/{driver.id}?token={customer.token}", 4403),
        (f"/ws/orders/{oid}?token={other.token}", 4404),          # someone else's order
    ]
    for url, code in cases:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(url) as ws:
                ws.receive_json()
        assert exc.value.code == code, url
