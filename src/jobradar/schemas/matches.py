from pydantic import BaseModel, ConfigDict

from jobradar.schemas.jobs import JobRead


class MatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score: int
    reason: str | None = None
    matching_skills: list[str] = []
    gaps: str | None = None
    info_level: str | None = None
    saved: bool = False
    applied_date: str | None = None
    job: JobRead


class MatchList(BaseModel):
    total: int
    items: list[MatchRead]


class TrackerUpdate(BaseModel):
    saved: bool | None = None
    applied: bool | None = None


class TaskAccepted(BaseModel):
    task_id: str
    detail: str
