from celery import Celery

from backend.app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "avoria",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["backend.app.workers.tasks"],
)
celery_app.conf.update(
    task_default_queue=settings.celery_task_default_queue,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_publish_retry=False,
    result_expires=settings.celery_result_expires_seconds,
    result_extended=True,
)

