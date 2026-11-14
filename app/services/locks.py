"""Distributed lock on Redis (SET NX EX + safe compare-and-delete release).

Works across many API replicas because every replica talks to the same Redis.
"""
import uuid

from redis.asyncio import Redis

# Delete the key ONLY if it still holds our token. Done in Lua so the
# "check" and the "delete" are one atomic step inside Redis. Without this,
# we could delete a lock that expired and was re-acquired by someone else.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def acquire_lock(r: Redis, key: str, ttl_s: int) -> str | None:
    """Returns a token if we got the lock, else None. Never blocks."""
    token = uuid.uuid4().hex
    # NX = only set if the key doesn't exist; EX = auto-expire (crash safety)
    ok = await r.set(key, token, nx=True, ex=ttl_s)
    return token if ok else None


async def release_lock(r: Redis, key: str, token: str) -> None:
    await r.eval(_RELEASE_SCRIPT, 1, key, token)
