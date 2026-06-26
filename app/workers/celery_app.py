"""
Celery app instance. Video processing (audio extraction, transcription,
AI generation, publishing) all run as background tasks so the upload
request returns immediately rather than blocking on a multi-minute
pipeline.
"""
from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "yt_ai_manager",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Both task modules must be listed so the worker registers every
    # task at startup. publish_pipeline is imported lazily (inside a
    # function in video_pipeline.py) to avoid a circular import, which
    # means a worker process would never load it on its own and would
    # silently drop every publish_video task with "Received unregistered
    # task" — found by actually running a worker and queuing a real task.
    include=["app.workers.video_pipeline", "app.workers.publish_pipeline"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Long video processing tasks shouldn't be silently killed
    task_time_limit=60 * 30,  # 30 min hard limit
    task_soft_time_limit=60 * 25,
)
