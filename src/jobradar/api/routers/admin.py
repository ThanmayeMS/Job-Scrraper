from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from jobradar.api.deps import AdminUser, DbSession
from jobradar.db.models import ScrapeLog
from jobradar.scrapers import SCRAPER_REGISTRY

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ScrapeRequest(BaseModel):
    companies: list[str] | None = None  # defaults to all registered scrapers


class ScrapeLogRead(BaseModel):
    company: str
    run_date: str
    new_jobs: int
    status: str


@router.get("/scrapers", response_model=list[str])
def list_scrapers(_: AdminUser):
    return sorted(SCRAPER_REGISTRY.keys())


@router.post("/scrape", status_code=202)
def trigger_scrape(payload: ScrapeRequest, _: AdminUser):
    from jobradar.workers.tasks import scrape_company_task

    companies = payload.companies or list(SCRAPER_REGISTRY.keys())
    unknown = [c for c in companies if c not in SCRAPER_REGISTRY]
    if unknown:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"Unknown scrapers: {unknown}")

    try:
        task_ids = {c: str(scrape_company_task.delay(c).id) for c in companies}
    except Exception as exc:
        return {"detail": f"Could not enqueue scrape: {exc}", "tasks": {}}
    return {"detail": "Scrape enqueued", "tasks": task_ids}


@router.post("/embed", status_code=202)
def trigger_embed(_: AdminUser):
    from jobradar.workers.tasks import embed_new_jobs_task

    try:
        return {"detail": "Embedding enqueued", "task_id": str(embed_new_jobs_task.delay().id)}
    except Exception as exc:
        return {"detail": f"Could not enqueue embedding: {exc}", "task_id": ""}


@router.get("/scrape-logs", response_model=list[ScrapeLogRead])
def scrape_logs(db: DbSession, _: AdminUser):
    rows = db.scalars(select(ScrapeLog).order_by(ScrapeLog.id.desc()).limit(50)).all()
    return [
        ScrapeLogRead(
            company=r.company, run_date=str(r.run_date), new_jobs=r.new_jobs, status=r.status
        )
        for r in rows
    ]
