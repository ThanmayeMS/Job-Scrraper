"""Celery application + beat schedule.

Beat mirrors the original roadmap's cron plan: scrape everything each morning,
then embed the new jobs shortly after.
"""

from celery import Celery
from celery.schedules import crontab

from jobradar.config import settings

celery_app = Celery(
    "jobradar",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["jobradar.workers.tasks"],
)

# In development (default) run tasks eagerly/inline so the app works with NO Redis or
# worker running. In production (ENVIRONMENT=production) tasks go to the real broker.
_eager = settings.tasks_eager

celery_app.conf.update(
    task_always_eager=_eager,
    task_eager_propagates=False,
    task_track_started=True,
    task_time_limit=60 * 45,
    task_soft_time_limit=60 * 40,
    worker_max_tasks_per_child=50,
    timezone="Asia/Kolkata",
    enable_utc=True,
    beat_schedule={
        "daily-scrape-all": {
            "task": "jobradar.workers.tasks.daily_scrape_all",
            "schedule": crontab(hour=7, minute=0),
        },
        "daily-embed-new-jobs": {
            "task": "jobradar.workers.tasks.embed_new_jobs_task",
            "schedule": crontab(hour=8, minute=0),
        },
    },
)
