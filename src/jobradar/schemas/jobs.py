from datetime import date

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    title: str
    apply_url: str
    locations: str | None = None
    posted_date: str | None = None
    fetched_date: date | None = None
    description: str | None = None


class JobList(BaseModel):
    total: int
    items: list[JobRead]
