import logging
import os

from rq import SpawnWorker, Worker
from rq.serializers import JSONSerializer

from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging
from backend.app.infrastructure.redis import get_sync_redis


def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger(__name__)

    worker_class = SpawnWorker if os.name == "nt" else Worker
    logger.info(
        "Starting RQ worker",
        extra={"queue": settings.rq_queue_name, "worker_class": worker_class.__name__},
    )
    worker = worker_class(
        [settings.rq_queue_name],
        connection=get_sync_redis(),
        serializer=JSONSerializer,
    )
    worker.work()


if __name__ == "__main__":
    run_worker()

