"""ORM models.

Design notes
------------
* `Job` is global and deduplicated on `apply_url` (same key the legacy JSON
  pipeline used). Its `embedding` + `work_profile` are computed once per job.
* Scoring is now **per user** (`JobScore`) instead of a single hard-coded resume,
  which is what makes this a real multi-user product.
* `raw` (JSONB) keeps the full company-specific payload so we never lose the
  "each company has its own schema" fidelity from the original design.
"""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jobradar.config import settings
from jobradar.db.base import Base

EMBED_DIM = settings.embedding_dim


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profile: Mapped["UserProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserProfile(Base):
    """One CV/resume per user, plus its extracted work-profile and embedding."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    resume_text: Mapped[str | None] = mapped_column(Text, default=None)
    work_profile: Mapped[str | None] = mapped_column(Text, default=None)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    apply_url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    company: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512))
    locations: Mapped[str | None] = mapped_column(String(512), default=None)
    posted_date: Mapped[str | None] = mapped_column(String(32), default=None)
    fetched_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    description: Mapped[str | None] = mapped_column(Text, default=None)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    work_profile: Mapped[str | None] = mapped_column(Text, default=None)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_job_scores_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    matching_skills: Mapped[list] = mapped_column(JSON, default=list)
    gaps: Mapped[str | None] = mapped_column(Text, default=None)
    info_level: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tracker(Base):
    """Per-user bookmark + applied state (replaces jobradar_tracker.json)."""

    __tablename__ = "trackers"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_tracker_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_date: Mapped[date | None] = mapped_column(Date, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(128), index=True)
    run_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(255), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
