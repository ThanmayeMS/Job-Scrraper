"""Seed a demo admin user + a couple of sample jobs — no scraping or LLM required.

    python scripts/seed_demo.py

Admin login:  admin@jobradar.dev / adminadmin  (change before any real deployment)
"""

from sqlalchemy import select

from jobradar.bootstrap import DEMO_JOBS
from jobradar.core.security import hash_password
from jobradar.db.base import SessionLocal
from jobradar.db.models import Job, User


def main() -> None:
    db = SessionLocal()
    try:
        if not db.scalar(select(User).where(User.email == "admin@jobradar.dev")):
            db.add(
                User(
                    email="admin@jobradar.dev",
                    hashed_password=hash_password("adminadmin"),
                    full_name="Demo Admin",
                    is_superuser=True,
                )
            )
        for j in DEMO_JOBS:
            if not db.scalar(select(Job).where(Job.apply_url == j["apply_url"])):
                db.add(
                    Job(
                        apply_url=j["apply_url"],
                        company=j["company"],
                        title=j["title"],
                        locations=j["locations"],
                        description=j["about_the_job"],
                        raw=j,
                    )
                )
        db.commit()
        print("Seeded demo admin + sample jobs.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
