from functools import lru_cache

from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from backend.app.core.config import get_settings


@lru_cache
def get_sync_redis() -> Redis:
    # RQ expects a binary Redis connection; text decoding is reserved for the
    # API-side client that will hold temporary state and progress messages.
    return Redis.from_url(get_settings().redis_url)


@lru_cache
def get_async_redis() -> AsyncRedis:
    return AsyncRedis.from_url(get_settings().redis_url, decode_responses=True)


async def close_redis_connections() -> None:
    if get_async_redis.cache_info().currsize:
        await get_async_redis().aclose()
        get_async_redis.cache_clear()
    if get_sync_redis.cache_info().currsize:
        get_sync_redis().close()
        get_sync_redis.cache_clear()
