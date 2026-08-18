from functools import lru_cache

from rq import Queue
from rq.serializers import JSONSerializer

from backend.app.core.config import get_settings
from backend.app.infrastructure.redis import get_sync_redis


@lru_cache
def get_media_queue() -> Queue:
    settings = get_settings()
    return Queue(
        name=settings.rq_queue_name,
        connection=get_sync_redis(),
        serializer=JSONSerializer,
    )

