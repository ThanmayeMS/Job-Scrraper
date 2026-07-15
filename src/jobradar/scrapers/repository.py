"""Postgres-backed replacement for the legacy core/db.py.

`exists()` + `upsert()` give scrapers the same dedup-on-apply_url semantics they had
with the JSON dict, but writes now land in a transactional database. Thread-safety and
crash-safety are the database's job now — no manual locks or atomic-file dance needed.
"""

import logging

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from jobradar.db.models import Job, ScrapeLog

log = logging.getLogger(__name__)

# Fields we promote from the company-specific payload into normalized columns.
_DESCRIPTION_FIELDS = (
    "about_the_job",
    "description",
    "job_description",
    "summary",
    "overview",
)


class JobRepository:
    def __init__(self, db: Session):
        self.db = db

    def exists(self, apply_url: str) -> bool:
        return bool(self.db.scalar(select(exists().where(Job.apply_url == apply_url))))

    def upsert(self, data: dict) -> bool:
        """Insert a job if its apply_url is new. Returns True if inserted."""
        url = (data.get("apply_url") or "").strip()
        if not url or self.exists(url):
            return False

        description = next((data[f] for f in _DESCRIPTION_FIELDS if data.get(f)), None)
        job = Job(
            apply_url=url,
            company=data.get("company", ""),
            title=data.get("title", ""),
            locations=data.get("locations"),
            posted_date=data.get("posted_date"),
            description=description,
            raw=data,
        )
        self.db.add(job)
        self.db.commit()
        return True

    def log_run(self, company: str, new_jobs: int, status: str = "ok") -> None:
        self.db.add(ScrapeLog(company=company, new_jobs=new_jobs, status=status))
        self.db.commit()
        log.info("[scrape] %s +%d (%s)", company, new_jobs, status)
