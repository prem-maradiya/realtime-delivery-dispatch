from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Driver, DriverStatus
from app.redis_client import get_redis
from app.schemas import DriverCreate, DriverOut, DriverStatusUpdate, Point
from app.services import drivers as driver_service
from app.services import location
from app.services.errors import Conflict, NotFound

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.post("", response_model=DriverOut, status_code=status.HTTP_201_CREATED)
async def register_driver(data: DriverCreate, session: AsyncSession = Depends(get_session)):
    driver = Driver(name=data.name, phone=data.phone, status=DriverStatus.OFFLINE)
    session.add(driver)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "phone already registered")
    return driver


@router.get("", response_model=list[DriverOut])
async def list_drivers(session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis)):
    drivers = (await session.execute(select(Driver).order_by(Driver.id))).scalars().all()
    # Overlay live locations from Redis (fresher than the periodically-persisted DB copy).
    pipe = r.pipeline()
    for d in drivers:
        pipe.hgetall(location.driver_key(d.id))
    live = await pipe.execute()
    out = []
    for d, state in zip(drivers, live):
        item = DriverOut.model_validate(d)
        if "lat" in state:
            item.last_lat, item.last_lng = float(state["lat"]), float(state["lng"])
        out.append(item)
    return out


@router.get("/{driver_id}", response_model=DriverOut)
async def get_driver(driver_id: int, session: AsyncSession = Depends(get_session)):
    driver = await session.get(Driver, driver_id)
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "driver not found")
    return driver


@router.post("/{driver_id}/status", response_model=DriverOut)
async def set_driver_status(
    driver_id: int, data: DriverStatusUpdate,
    session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis),
):
    try:
        return await driver_service.change_status(session, r, driver_id, DriverStatus(data.status))
    except NotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except Conflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.post("/{driver_id}/location", status_code=status.HTTP_204_NO_CONTENT)
async def update_location(
    driver_id: int, point: Point,
    session: AsyncSession = Depends(get_session), r: Redis = Depends(get_redis),
):
    """REST fallback for drivers who can't hold a WebSocket open."""
    if await session.get(Driver, driver_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "driver not found")
    await location.record_location(r, driver_id, point.lat, point.lng)
