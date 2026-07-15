"""Celery tasks: scraping, embedding, per-user profiling and matching.

Each task opens and closes its own DB session (workers are sync, one session per task).
"""

import logging

from sqlalchemy import select

from jobradar.db.base import SessionLocal
from jobradar.db.models import Job, UserProfile
from jobradar.scrapers import SCRAPER_REGISTRY
from jobradar.scrapers.repository import JobRepository
from jobradar.services.embeddings import embed_texts
from jobradar.services.matching import ensure_user_profile, run_user_matching
from jobradar.services.scoring import extract_job_text
from jobradar.workers.celery_app import celery_app

log = logging.getLogger(__name__)

EMBED_BATCH = 128


@celery_app.task(name="jobradar.workers.tasks.scrape_company_task")
def scrape_company_task(company: str) -> int:
    if company not in SCRAPER_REGISTRY:
        log.warning("Unknown scraper: %s", company)
        return 0
    db = SessionLocal()
    try:
        return SCRAPER_REGISTRY[company]().scrape(JobRepository(db))
    finally:
        db.close()


@celery_app.task(name="jobradar.workers.tasks.daily_scrape_all")
def daily_scrape_all() -> dict:
    return {c: scrape_company_task.delay(c).id for c in SCRAPER_REGISTRY}


@celery_app.task(name="jobradar.workers.tasks.embed_new_jobs_task")
def embed_new_jobs_task() -> int:
    """Embed every job that doesn't yet have an embedding, in batches."""
    db = SessionLocal()
    embedded = 0
    try:
        while True:
            jobs = list(db.scalars(select(Job).where(Job.embedding.is_(None)).limit(EMBED_BATCH)))
            if not jobs:
                break
            texts = [extract_job_text(j.raw or {}) or j.title for j in jobs]
            vectors = embed_texts(texts)
            for job, vector in zip(jobs, vectors, strict=True):
                job.embedding = vector
            db.commit()
            embedded += len(jobs)
        log.info("Embedded %d jobs", embedded)
        return embedded
    finally:
        db.close()


@celery_app.task(name="jobradar.workers.tasks.build_user_profile_task")
def build_user_profile_task(user_id: int) -> bool:
    db = SessionLocal()
    try:
        profile = ensure_user_profile(db, user_id)
        return profile is not None
    finally:
        db.close()


@celery_app.task(name="jobradar.workers.tasks.score_user_task")
def score_user_task(user_id: int) -> int:
    db = SessionLocal()
    try:
        return run_user_matching(db, user_id)
    finally:
        db.close()


@celery_app.task(name="jobradar.workers.tasks.score_all_users_task")
def score_all_users_task() -> dict:
    db = SessionLocal()
    try:
        user_ids = list(db.scalars(select(UserProfile.user_id)))
    finally:
        db.close()
    return {str(uid): score_user_task.delay(uid).id for uid in user_ids}
