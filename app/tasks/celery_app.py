from celery import Celery
from app.config import settings

celery = Celery(
    "ipxe_manager",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.jobs"],
)

# extract_timeout = 3600 s (1 h) par défaut, configurable dans .env
_soft = settings.extract_timeout          # signal SoftTimeLimitExceeded → cleanup
_hard = settings.extract_timeout + 300    # +5 min → SIGKILL forcé

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=86400,       # 24 h
    task_soft_time_limit=_soft,
    task_time_limit=_hard,
    worker_prefetch_multiplier=1,   # 1 tâche à la fois par worker
)
