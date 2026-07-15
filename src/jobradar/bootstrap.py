"""First-run bootstrap helpers for no-shell deployments.

Free hosting plans often don't provide an interactive shell. This lets a deployment
create its first admin user and optional sample jobs from environment variables.
"""

import logging

from sqlalchemy import select

from jobradar.config import settings
from jobradar.core.security import hash_password
from jobradar.db.base import SessionLocal
from jobradar.db.models import Job, User

log = logging.getLogger(__name__)

DEMO_JOBS = [
    {
        "apply_url": "https://example.com/jobs/de-1",
        "company": "Amazon",
        "title": "Data Engineer, Analytics",
        "locations": "Bengaluru, India",
        "about_the_job": "Build and own ETL pipelines and data infrastructure for analytics.",
    },
    {
        "apply_url": "https://example.com/jobs/ba-2",
        "company": "Google",
        "title": "Business Intelligence Analyst",
        "locations": "Hyderabad, India",
        "about_the_job": "Design KPI dashboards and reporting for leadership decisions.",
    },
]


def bootstrap_initial_data() -> None:
    """Create/update env-configured admin and optional sample jobs.

    Idempotent by design: safe to run on every app startup.
    """
    needs_admin = bool(settings.admin_email and settings.admin_password)
    if not needs_admin and not settings.seed_demo_data:
        return

    db = SessionLocal()
    try:
        if needs_admin:
            user = db.scalar(select(User).where(User.email == settings.admin_email))
            if user is None:
                user = User(email=settings.admin_email)
                db.add(user)
                log.info("Created bootstrap admin user %s", settings.admin_email)
            user.hashed_password = hash_password(settings.admin_password)
            user.full_name = settings.admin_full_name
            user.is_superuser = True
            user.is_active = True

        if settings.seed_demo_data:
            for job in DEMO_JOBS:
                if not db.scalar(select(Job).where(Job.apply_url == job["apply_url"])):
                    db.add(
                        Job(
                            apply_url=job["apply_url"],
                            company=job["company"],
                            title=job["title"],
                            locations=job["locations"],
                            description=job["about_the_job"],
                            raw=job,
                        )
                    )

        db.commit()
    except Exception:
        db.rollback()
        log.exception("Initial data bootstrap failed")
        raise
    finally:
        db.close()
