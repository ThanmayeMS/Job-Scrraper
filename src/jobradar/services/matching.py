"""Per-user matching pipeline: ensure profile -> embed -> recall -> LLM score.

This is the multi-user replacement for the single hard-coded resume in the original
score_jobs_openai.py.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.config import settings
from jobradar.db.models import JobScore, UserProfile
from jobradar.services.cv_extract import build_work_profile
from jobradar.services.embeddings import embed_text, recall_top_jobs
from jobradar.services.scoring import score_job

log = logging.getLogger(__name__)


def ensure_user_profile(db: Session, user_id: int) -> UserProfile | None:
    """Make sure the user's work-profile + embedding exist. Returns None if no CV yet."""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None or not profile.resume_text:
        return None

    changed = False
    if not profile.work_profile:
        profile.work_profile = build_work_profile(profile.resume_text)
        changed = True
    if profile.embedding is None:
        profile.embedding = embed_text(profile.work_profile or profile.resume_text)
        changed = True
    if changed:
        db.commit()
    return profile


def run_user_matching(db: Session, user_id: int) -> int:
    """Score the user's top candidate jobs. Returns the number of new scores written."""
    profile = ensure_user_profile(db, user_id)
    if profile is None:
        log.info("User %s has no CV — skipping matching", user_id)
        return 0

    candidates = recall_top_jobs(db, profile.embedding, settings.max_llm_scores_per_run)
    already_scored = set(db.scalars(select(JobScore.job_id).where(JobScore.user_id == user_id)))

    scored = 0
    for job in candidates:
        if job.id in already_scored:
            continue
        try:
            result = score_job(profile.resume_text, job.raw or {})
        except Exception as exc:
            log.warning("Scoring failed for job %s: %s", job.id, exc)
            continue
        db.add(
            JobScore(
                user_id=user_id,
                job_id=job.id,
                score=int(result.get("score", 0) or 0),
                reason=result.get("reason"),
                matching_skills=result.get("matching_skills", []),
                gaps=result.get("gaps"),
                info_level=result.get("info_level"),
            )
        )
        db.commit()
        scored += 1

    log.info("User %s matching complete — %d new scores", user_id, scored)
    return scored
