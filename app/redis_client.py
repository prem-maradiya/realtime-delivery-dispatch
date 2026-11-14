"""Single shared async Redis client for the process."""
from redis.asyncio import Redis

from app.config import settings


class RedisHolder:
    client: Redis | None = None

    async def init(self) -> Redis:
        if settings.redis_url.startswith("fakeredis"):
            # In-memory Redis used by the test suite
            import fakeredis

            self.client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        else:
            self.client = Redis.from_url(settings.redis_url, decode_responses=True)
        await self.client.ping()
        return self.client

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None


redis_holder = RedisHolder()


def get_redis() -> Redis:
    """FastAPI dependency."""
    assert redis_holder.client is not None, "Redis not initialised"
    return redis_holder.client
