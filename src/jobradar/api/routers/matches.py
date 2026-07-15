from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, select

from jobradar.api.deps import CurrentUser, DbSession
from jobradar.db.models import Job, JobScore, Tracker
from jobradar.schemas.jobs import JobRead
from jobradar.schemas.matches import MatchList, MatchRead, TaskAccepted, TrackerUpdate

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("", response_model=MatchList)
def list_matches(
    db: DbSession,
    user: CurrentUser,
    min_score: Annotated[int, Query(ge=0, le=10)] = 0,
    company: str | None = None,
    saved: bool | None = None,
    applied: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    tracker_join = and_(Tracker.job_id == JobScore.job_id, Tracker.user_id == user.id)
    stmt = (
        select(JobScore, Job, Tracker)
        .join(Job, Job.id == JobScore.job_id)
        .outerjoin(Tracker, tracker_join)
        .where(JobScore.user_id == user.id, JobScore.score >= min_score)
    )
    if company:
        stmt = stmt.where(Job.company == company)
    if saved is not None:
        stmt = (
            stmt.where(Tracker.saved.is_(True))
            if saved
            else stmt.where((Tracker.saved.is_(False)) | (Tracker.id.is_(None)))
        )
    if applied is not None:
        stmt = (
            stmt.where(Tracker.applied_date.is_not(None))
            if applied
            else stmt.where(Tracker.applied_date.is_(None))
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(JobScore.score.desc(), Job.fetched_date.desc()).limit(limit).offset(offset)
    ).all()

    items = [
        MatchRead(
            score=score.score,
            reason=score.reason,
            matching_skills=score.matching_skills or [],
            gaps=score.gaps,
            info_level=score.info_level,
            saved=bool(tracker and tracker.saved),
            applied_date=str(tracker.applied_date) if tracker and tracker.applied_date else None,
            job=JobRead.model_validate(job),
        )
        for score, job, tracker in rows
    ]
    return MatchList(total=total, items=items)


@router.post("/run", response_model=TaskAccepted, status_code=202)
def run_matching(user: CurrentUser):
    """Kick off the per-user matching pipeline (embed CV -> recall -> LLM score)."""
    from jobradar.workers.tasks import score_user_task

    try:
        async_result = score_user_task.delay(user.id)
        return TaskAccepted(task_id=str(async_result.id), detail="Matching started")
    except Exception as exc:
        return TaskAccepted(task_id="", detail=f"Could not start matching: {exc}")


@router.put("/{job_id}/tracker", response_model=MatchRead | None)
def update_tracker(job_id: int, payload: TrackerUpdate, db: DbSession, user: CurrentUser):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    tracker = db.scalar(select(Tracker).where(Tracker.user_id == user.id, Tracker.job_id == job_id))
    if tracker is None:
        tracker = Tracker(user_id=user.id, job_id=job_id)
        db.add(tracker)

    if payload.saved is not None:
        tracker.saved = payload.saved
    if payload.applied is not None:
        tracker.applied_date = date.today() if payload.applied else None

    db.commit()

    score = db.scalar(
        select(JobScore).where(JobScore.user_id == user.id, JobScore.job_id == job_id)
    )
    if not score:
        return None
    return MatchRead(
        score=score.score,
        reason=score.reason,
        matching_skills=score.matching_skills or [],
        gaps=score.gaps,
        info_level=score.info_level,
        saved=bool(tracker.saved),
        applied_date=str(tracker.applied_date) if tracker.applied_date else None,
        job=JobRead.model_validate(job),
    )
