from fastapi import APIRouter
from sqlalchemy import text

from jobradar.api.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/db")
def health_db(db: DbSession) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
