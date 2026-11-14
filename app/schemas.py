"""Pydantic models: request validation and response shapes."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import DriverStatus, OrderStatus


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


# ---------- Drivers ----------
class DriverCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=5, max_length=20)


class DriverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    status: DriverStatus
    last_lat: float | None
    last_lng: float | None
    created_at: datetime


class DriverStatusUpdate(BaseModel):
    # BUSY is never set by hand: only the dispatcher sets it.
    status: Literal["available", "offline"]


# ---------- Orders ----------
class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)
    pickup: Point
    dropoff: Point


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    status: OrderStatus
    driver_id: int | None
    dispatch_attempts: int
    assignment_distance_km: float | None
    created_at: datetime
    assigned_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None


class OrderEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OrderStatus
    driver_id: int | None
    note: str | None
    created_at: datetime


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


# ---------- WebSocket messages ----------
class LocationMessage(BaseModel):
    type: Literal["location"]
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
