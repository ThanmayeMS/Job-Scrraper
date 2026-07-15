from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from jobradar.api.deps import DbSession
from jobradar.db.models import Job
from jobradar.schemas.jobs import JobList, JobRead

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=JobList)
def list_jobs(
    db: DbSession,
    company: str | None = None,
    q: str | None = Query(None, description="Search in title/company"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = []
    if company:
        filters.append(Job.company == company)
    if q:
        like = f"%{q}%"
        filters.append(or_(Job.title.ilike(like), Job.company.ilike(like)))

    total = db.scalar(select(func.count()).select_from(Job).where(*filters)) or 0
    rows = db.scalars(
        select(Job)
        .where(*filters)
        .order_by(Job.fetched_date.desc(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return JobList(total=total, items=[JobRead.model_validate(r) for r in rows])


@router.get("/companies", response_model=list[str])
def list_companies(db: DbSession):
    return list(db.scalars(select(Job.company).distinct().order_by(Job.company)).all())


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: DbSession):
    from fastapi import HTTPException

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
